from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .base import RawVacancy, SourceResult, VacancySource, vacancy_exists


BASE_URL = "https://yandex.ru"
LIST_URL = "https://yandex.ru/jobs/vacancies"
PROFESSIONS = ("project-manager", "tech-manager")
REQUEST_TIMEOUT = 30.0
MAX_PAGES = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

VACANCY_PATH_RE = re.compile(
    r"^/jobs/vacancies/(?P<slug>.+)-(?P<id>\d+)/?$",
    re.IGNORECASE,
)

TARGET_TITLE_RE = re.compile(
    r"(?:"
    r"project\s*manager|program\s*manager|programme\s*manager|"
    r"delivery\s*manager|technical\s*manager|"
    r"менеджер\s+(?:it[-‑ ]?)?проект|менеджер\s+проект|"
    r"техническ(?:ий|ого)\s+менеджер|"
    r"руководител[ья]\s+(?:it[-‑ ]?)?проект|"
    r"руководител[ья]\s+проект|проектн(?:ый|ого)\s+офис|"
    r"program\s+management|project\s+management"
    r")",
    re.IGNORECASE,
)

REJECT_TITLE_RE = re.compile(
    r"(?:стаж[её]р|стажиров|практикант|intern(?:ship)?|trainee|"
    r"\bjunior\b|\bjr\.?\b)",
    re.IGNORECASE,
)


class ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        absolute = urljoin(BASE_URL, href)
        parsed = urlparse(absolute)
        if parsed.netloc not in {"yandex.ru", "www.yandex.ru"}:
            return
        if VACANCY_PATH_RE.match(parsed.path):
            self.links.append(absolute.split("?", 1)[0])


class VacancyTextParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "br", "div", "h1", "h2", "h3", "li", "main",
        "p", "section", "ul", "ol",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.h1_parts: list[str] = []
        self.skip_depth = 0
        self.in_h1 = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
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
        if tag == "h1":
            self.in_h1 = False
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
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


def vacancy_id_from_url(url: str) -> str | None:
    match = VACANCY_PATH_RE.match(urlparse(url).path)
    return match.group("id") if match else None


def is_target_title(title: str) -> bool:
    value = title or ""
    return bool(TARGET_TITLE_RE.search(value)) and not bool(REJECT_TITLE_RE.search(value))


class YandexSource(VacancySource):
    name = "yandex"

    @staticmethod
    def _listing_params(page: int) -> list[tuple[str, str]]:
        params = [("profession", item) for item in PROFESSIONS]
        if page > 1:
            params.append(("page", str(page)))
        return params

    def _collect_links(self, client: httpx.Client) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for page_number in range(1, MAX_PAGES + 1):
            response = client.get(
                LIST_URL,
                params=self._listing_params(page_number),
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            parser = ListingParser()
            parser.feed(response.text)
            page_new = 0

            for url in parser.links:
                external_id = vacancy_id_from_url(url)
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                result.append(url)
                page_new += 1

            print(
                f"[YANDEX] Страница {page_number}: "
                f"новых ссылок {page_new}, всего {len(result)}"
            )
            if page_new == 0:
                break

        return result

    @staticmethod
    def _fetch_vacancy(client: httpx.Client, url: str) -> tuple[str, str]:
        response = client.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        parser = VacancyTextParser()
        parser.feed(response.text)
        title = parser.title
        text = parser.text

        if not title:
            match = re.search(
                r"<title[^>]*>(.*?)</title>",
                response.text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if match:
                title = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
                title = " ".join(title.split())
                title = re.sub(
                    r"^Вакансия\s+[«\"]?|[»\"]?\s+в\s+Яндексе.*$",
                    "",
                    title,
                    flags=re.IGNORECASE,
                ).strip()

        if not title or len(text) < 200:
            raise RuntimeError(f"Не удалось разобрать карточку Яндекса: {url}")
        return title, text

    def collect(self) -> SourceResult:
        result = SourceResult()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }

        with httpx.Client(headers=headers, follow_redirects=True) as client:
            links = self._collect_links(client)
            print(f"[YANDEX] Найдено уникальных карточек: {len(links)}")

            for index, url in enumerate(links, start=1):
                external_id = vacancy_id_from_url(url)
                if not external_id:
                    continue

                if vacancy_exists(self.name, external_id):
                    result.skipped += 1
                    continue

                try:
                    title, description = self._fetch_vacancy(client, url)
                    if not is_target_title(title):
                        result.skipped += 1
                        print(f"[{index}/{len(links)}] SKIP TITLE: {title}")
                        continue

                    result.vacancies.append(
                        RawVacancy(
                            source=self.name,
                            external_id=external_id,
                            title=title,
                            company="Яндекс",
                            url=url,
                            description=description,
                        )
                    )
                except Exception as exc:
                    result.errors += 1
                    print(
                        f"[{index}/{len(links)}] ERROR {url}: "
                        f"{type(exc).__name__}: {exc}"
                    )

        return result
