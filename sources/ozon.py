from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from app.vacancy_url import canonicalize_url

from .base import RawVacancy, SourceResult, VacancySource, vacancy_exists
from .title_policy import is_management_tech_fallback


# Ozon's first-party career domains currently block the production egress IP
# with an anti-bot challenge. Discovery/content therefore uses a read-only
# indexed mirror, while Vacancy.url always stores the first-party Ozon URL.
MIRROR_BASE_URL = "https://over-offer.ru"
MIRROR_LIST_URL = f"{MIRROR_BASE_URL}/"
REQUEST_TIMEOUT = 30.0
MAX_PAGES_PER_QUERY = 8
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

SEARCH_TERMS = (
    "Ozon project",
    "Ozon менеджер проектов",
    "Ozon руководитель проектов",
    "Ozon Technical Project Manager",
    "Ozon product",
    "Ozon продакт",
    "Ozon delivery",
    "Ozon технический менеджер",
)

MIRROR_DETAIL_PATH_RE = re.compile(
    r"^/vacancies/(?P<id>\d+)/?$",
    re.IGNORECASE,
)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
CAREER_NUMERIC_RE = re.compile(
    r"/vacancy/(?:[^/?#]+-)?(?P<id>\d+)/?$",
    re.IGNORECASE,
)
OZON_TECH_RE = re.compile(
    r"/vacancies/(?P<id>[0-9a-f-]{36})/?$",
    re.IGNORECASE,
)

TARGET_TITLE_RE = re.compile(
    r"(?:"
    r"project\s*manager|senior\s+project\s*manager|"
    r"technical\s*project\s*manager|it\s*project\s*manager|"
    r"program\s*manager|programme\s*manager|\bpmo\b|"
    r"delivery\s*manager|delivery\s*lead|"
    r"product\s*manager|product\s*owner|product\s*lead|"
    r"head\s+of\s+product|technical\s*product\s*manager|"
    r"менеджер\s+(?:it[-‑ ]?)?проект|менеджер\s+проект|"
    r"руководител[ья]\s+(?:it[-‑ ]?)?проект|"
    r"руководител[ья]\s+проект|"
    r"руководител[ья]\s+по\s+управлению\s+проект|"
    r"руководител[ья]\s+групп[ыа]\s+проект|"
    r"старш(?:ий|его)\s+менеджер\s+проект|"
    r"ведущ(?:ий|его)\s+менеджер\s+проект|"
    r"техническ(?:ий|ого)\s+менеджер\s+проект|"
    r"менеджер\s+(?:по\s+)?продукт|"
    r"продакт[-\s]?менеджер|"
    r"продуктов(?:ый|ого)\s+менеджер|"
    r"руководител[ья]\s+(?:групп[ыа]\s+)?продукт|"
    r"руководител[ья]\s+групп[ыа]\s+продакт|"
    r"ведущ(?:ий|его)\s+менеджер\s+по\s+продукт|"
    r"старш(?:ий|его)\s+менеджер\s+по\s+продукт"
    r")",
    re.IGNORECASE,
)
REJECT_TITLE_RE = re.compile(
    r"(?:стаж[её]р|стажиров|практикант|intern(?:ship)?|trainee|"
    r"\bjunior\b|\bjr\.?\b|"
    r"продаж|sales|маркетинг|marketing)",
    re.IGNORECASE,
)


class ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlparse(urljoin(MIRROR_BASE_URL, href))
        if parsed.netloc.lower() != urlparse(MIRROR_BASE_URL).netloc:
            return
        if not MIRROR_DETAIL_PATH_RE.match(parsed.path):
            return
        self._href = parsed.path
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        value = " ".join(html.unescape(data).split())
        if value:
            self._text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = " ".join(self._text).strip()
        self.items.append((self._href, title))
        self._href = None
        self._text = []


class DetailParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "br", "div", "h1", "h2", "h3",
        "li", "p", "section", "ul", "ol",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.article_depth = 0
        self.skip_depth = 0
        self.in_h1 = False
        self.parts: list[str] = []
        self.h1_parts: list[str] = []
        self.external_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs_dict = dict(attrs)

        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if tag == "article":
            self.article_depth += 1

        if tag == "a":
            href = attrs_dict.get("href")
            if href:
                absolute = urljoin(MIRROR_BASE_URL, href)
                host = urlparse(absolute).netloc.lower()
                if host in {
                    "career.ozon.ru",
                    "www.career.ozon.ru",
                    "ozon.tech",
                    "www.ozon.tech",
                }:
                    self.external_urls.append(absolute)

        if not self.article_depth:
            return

        if tag == "h1":
            self.in_h1 = True
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return

        if self.article_depth:
            if tag == "h1":
                self.in_h1 = False
            if tag in self.BLOCK_TAGS:
                self.parts.append("\n")

        if tag == "article" and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.article_depth:
            return
        value = " ".join(html.unescape(data).split())
        if not value:
            return
        self.parts.extend((value, " "))
        if self.in_h1:
            self.h1_parts.append(value)

    @property
    def title(self) -> str:
        return " ".join(self.h1_parts).strip()

    @property
    def text(self) -> str:
        lines: list[str] = []
        for raw in "".join(self.parts).splitlines():
            line = " ".join(raw.split())
            if line and (not lines or line != lines[-1]):
                lines.append(line)
        return "\n".join(lines)


def is_target_title(title: str) -> bool:
    value = " ".join((title or "").split())
    if not value or REJECT_TITLE_RE.search(value):
        return False
    return bool(TARGET_TITLE_RE.search(value)) or is_management_tech_fallback(value)


def mirror_id_from_url(url: str) -> str | None:
    match = MIRROR_DETAIL_PATH_RE.match(urlparse(url).path)
    return match.group("id") if match else None


def ozon_external_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host in {"career.ozon.ru", "www.career.ozon.ru"}:
        match = CAREER_NUMERIC_RE.search(parsed.path)
        if match:
            return match.group("id")
        tail = parsed.path.rstrip("/").split("/")[-1]
        if tail:
            return tail

    if host in {"ozon.tech", "www.ozon.tech"}:
        match = OZON_TECH_RE.search(parsed.path)
        if match and UUID_RE.match(match.group("id")):
            return match.group("id").lower()

    return None


def canonical_ozon_url(url: str) -> str | None:
    value = (url or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.netloc.lower() not in {
        "career.ozon.ru",
        "www.career.ozon.ru",
        "ozon.tech",
        "www.ozon.tech",
    }:
        return None
    return canonicalize_url(value.split("?", 1)[0].split("#", 1)[0])


def mirror_vacancy_active(page_html: str) -> bool:
    match = re.search(
        r"\bis_active\s*:\s*(true|false)",
        page_html,
        flags=re.IGNORECASE,
    )
    if not match:
        return True
    return match.group(1).lower() == "true"


class OzonSource(VacancySource):
    name = "ozon"

    @staticmethod
    def _listing_url(term: str, page: int) -> str:
        params: dict[str, str] = {"q": term}
        if page > 1:
            params["page"] = str(page)
        return f"{MIRROR_LIST_URL}?{urlencode(params)}"

    def _collect_mirror_links(
        self,
        client: httpx.Client,
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        seen: set[str] = set()

        for term in SEARCH_TERMS:
            idle = False
            for page in range(1, MAX_PAGES_PER_QUERY + 1):
                url = self._listing_url(term, page)
                response = client.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()

                parser = ListingParser()
                parser.feed(response.text)
                added = 0

                for path, title in parser.items:
                    mirror_id = mirror_id_from_url(path)
                    if not mirror_id or mirror_id in seen:
                        continue
                    if not is_target_title(title):
                        continue
                    seen.add(mirror_id)
                    result.append(
                        (urljoin(MIRROR_BASE_URL, path), title)
                    )
                    added += 1

                print(
                    f"[OZON] mirror query={term!r} page={page}: "
                    f"новых кандидатов {added}, всего {len(result)}"
                )

                if not parser.items:
                    idle = True
                    break

            if idle:
                continue

        return result

    @staticmethod
    def _fetch_mirror_vacancy(
        client: httpx.Client,
        url: str,
    ) -> tuple[str, str, str]:
        response = client.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        if not mirror_vacancy_active(response.text):
            raise RuntimeError("mirror_vacancy_inactive")

        parser = DetailParser()
        parser.feed(response.text)

        title = parser.title
        description = parser.text
        source_url = next(
            (
                canonical_ozon_url(item)
                for item in parser.external_urls
                if canonical_ozon_url(item)
            ),
            None,
        )

        if not source_url:
            raise RuntimeError("no_first_party_ozon_url")
        if not title or len(description) < 200:
            raise RuntimeError("mirror_vacancy_parse_failed")

        return title, description, source_url

    def collect(self) -> SourceResult:
        result = SourceResult()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        }

        print(
            "[OZON] First-party Ozon blocks current production egress; "
            "using over-offer.ru as read-only discovery/content mirror. "
            "Stored vacancy URL remains first-party Ozon."
        )

        with httpx.Client(
            headers=headers,
            follow_redirects=True,
        ) as client:
            candidates = self._collect_mirror_links(client)
            print(
                f"[OZON] Найдено уникальных mirror-кандидатов: "
                f"{len(candidates)}"
            )

            for index, (mirror_url, listing_title) in enumerate(
                candidates,
                start=1,
            ):
                try:
                    title, description, first_party_url = (
                        self._fetch_mirror_vacancy(
                            client,
                            mirror_url,
                        )
                    )

                    if not is_target_title(title):
                        result.skipped += 1
                        print(
                            f"[{index}/{len(candidates)}] "
                            f"SKIP TITLE: {title}"
                        )
                        continue

                    external_id = ozon_external_id(first_party_url)
                    if not external_id:
                        mirror_id = mirror_id_from_url(mirror_url)
                        external_id = (
                            f"mirror-{mirror_id}"
                            if mirror_id
                            else None
                        )
                    if not external_id:
                        result.errors += 1
                        print(
                            f"[{index}/{len(candidates)}] "
                            f"ERROR external id: {mirror_url}"
                        )
                        continue

                    if vacancy_exists(self.name, external_id):
                        result.skipped += 1
                        continue

                    result.vacancies.append(
                        RawVacancy(
                            source=self.name,
                            external_id=external_id,
                            title=title or listing_title,
                            company="Ozon",
                            url=first_party_url,
                            description=description,
                        )
                    )
                except RuntimeError as exc:
                    reason = str(exc)
                    if reason == "mirror_vacancy_inactive":
                        result.skipped += 1
                        continue
                    result.errors += 1
                    print(
                        f"[{index}/{len(candidates)}] ERROR {mirror_url}: "
                        f"{reason}"
                    )
                except Exception as exc:
                    result.errors += 1
                    print(
                        f"[{index}/{len(candidates)}] ERROR {mirror_url}: "
                        f"{type(exc).__name__}: {exc}"
                    )

        return result
