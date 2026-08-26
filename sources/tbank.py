from __future__ import annotations

import html
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .base import RawVacancy, SourceResult, VacancySource, vacancy_exists


BASE_URL = "https://www.tbank.ru"
LIST_URLS = (
    f"{BASE_URL}/career/vacancies/all/moscow/",
    f"{BASE_URL}/career/vacancies/it/",
)
REQUEST_TIMEOUT = 30.0
REQUEST_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
IT_VACANCY_PATH_RE = re.compile(
    rf"^/career/it/vacancy/[^/]+/[^/]+/(?P<id>{UUID_RE})/?$",
    re.IGNORECASE,
)
EMBEDDED_IT_VACANCY_RE = re.compile(
    rf"(?:https?://(?:www\.)?tbank\.ru)?(?P<path>/career/it/vacancy/[^\"'<>\s]+/[^\"'<>\s]+/(?P<id>{UUID_RE})/?)",
    re.IGNORECASE,
)

TARGET_TITLE_RE = re.compile(
    r"(?:"
    r"project\s*manager|program\s*manager|programme\s*manager|"
    r"product\s*manager|technical\s*product\s*manager|product\s*owner|"
    r"product\s*lead|head\s+of\s+product|delivery\s*manager|delivery\s*lead|"
    r"technical\s*project\s*manager|it\s*project\s*manager|"
    r"менеджер\s+(?:it[-‑ ]?)?проект|менеджер\s+проект|"
    r"руководител[ья]\s+(?:it[-‑ ]?)?проект|руководител[ья]\s+проект|"
    r"менеджер\s+(?:[A-Za-zА-Яа-я0-9]+[-‑–—])?продукт|"
    r"продакт[-\s]?менеджер|продуктов(?:ый|ого)\s+менеджер|"
    r"руководител[ья]\s+продукт|директор\s+проект|"
    r"старш(?:ий|его)\s+менеджер\s+проект"
    r")",
    re.IGNORECASE,
)

REJECT_TITLE_RE = re.compile(
    r"(?:стаж[её]р|стажиров|практикант|intern(?:ship)?|trainee|"
    r"\bjunior\b|\bjr\.?\b)",
    re.IGNORECASE,
)

INACTIVE_MARKERS = (
    "набор по вакансии закрыт",
    "вакансия закрыта",
    "вакансия уже закрыта",
    "вакансия не актуальна",
    "позиция закрыта",
)


class LinkParser(HTMLParser):
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
        if parsed.netloc.lower() not in {"tbank.ru", "www.tbank.ru"}:
            return
        if IT_VACANCY_PATH_RE.match(parsed.path):
            self.links.append(absolute.split("?", 1)[0].split("#", 1)[0])


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
    match = IT_VACANCY_PATH_RE.match(urlparse(url).path)
    return match.group("id") if match else None


def is_target_title(title: str) -> bool:
    value = title or ""
    return bool(TARGET_TITLE_RE.search(value)) and not bool(REJECT_TITLE_RE.search(value))


def is_inactive(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    return any(marker in normalized for marker in INACTIVE_MARKERS)


def extract_vacancy_links(page_html: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    parser = LinkParser()
    parser.feed(page_html)

    for url in parser.links:
        external_id = vacancy_id_from_url(url)
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)
        result.append(url)

    unescaped = html.unescape(page_html).replace("\\/", "/")
    for match in EMBEDDED_IT_VACANCY_RE.finditer(unescaped):
        external_id = match.group("id")
        if external_id in seen:
            continue
        seen.add(external_id)
        path = match.group("path")
        result.append(urljoin(BASE_URL, path))

    return result


def _get_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            response = client.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429 and attempt < REQUEST_RETRIES:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else attempt * 2.0
                print(f"[TBANK] HTTP 429 {url}; retry {attempt}/{REQUEST_RETRIES} через {delay:.0f} сек.")
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt >= REQUEST_RETRIES:
                break
            delay = attempt * 1.5
            print(
                f"[TBANK] REQUEST ERROR {url}: {type(exc).__name__}: {exc}; "
                f"retry {attempt}/{REQUEST_RETRIES} через {delay:.1f} сек."
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


class TBankSource(VacancySource):
    name = "tbank"

    def _collect_links(self, client: httpx.Client) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for list_url in LIST_URLS:
            try:
                response = _get_with_retry(client, list_url)
            except Exception as exc:
                print(
                    f"[TBANK] DISCOVERY ERROR {list_url}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            added = 0
            for url in extract_vacancy_links(response.text):
                external_id = vacancy_id_from_url(url)
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                result.append(url)
                added += 1

            print(
                f"[TBANK] Discovery {list_url}: "
                f"новых ссылок {added}, всего {len(result)}"
            )

        return result

    @staticmethod
    def _fetch_vacancy(client: httpx.Client, url: str) -> tuple[str, str]:
        response = _get_with_retry(client, url)

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
                    r"^Вакансия\s+|\s*[|—-]\s*Т-Банк.*$|\s*[|—-]\s*Тинькофф.*$",
                    "",
                    title,
                    flags=re.IGNORECASE,
                ).strip()

        if not title or len(text) < 200:
            raise RuntimeError(f"Не удалось разобрать карточку Т-Банка: {url}")

        return title, text

    def collect(self) -> SourceResult:
        result = SourceResult()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        with httpx.Client(headers=headers, follow_redirects=True) as client:
            links = self._collect_links(client)
            print(f"[TBANK] Найдено уникальных карточек: {len(links)}")

            for index, url in enumerate(links, start=1):
                external_id = vacancy_id_from_url(url)
                if not external_id:
                    continue

                if vacancy_exists(self.name, external_id):
                    result.skipped += 1
                    continue

                try:
                    title, description = self._fetch_vacancy(client, url)

                    if is_inactive(description):
                        result.skipped += 1
                        print(f"[{index}/{len(links)}] SKIP CLOSED: {title}")
                        continue

                    if not is_target_title(title):
                        result.skipped += 1
                        print(f"[{index}/{len(links)}] SKIP TITLE: {title}")
                        continue

                    result.vacancies.append(
                        RawVacancy(
                            source=self.name,
                            external_id=external_id,
                            title=title,
                            company="Т-Банк",
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
