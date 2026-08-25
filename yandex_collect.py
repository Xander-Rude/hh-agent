from __future__ import annotations

import html
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from app.db import SessionLocal, Vacancy


BASE_URL = "https://yandex.ru"
LIST_URL = "https://yandex.ru/jobs/vacancies"

# Яндекс сам объединяет эти две профессии в разделе «Управление проектами».
PROFESSIONS = (
    "project-manager",
    "tech-manager",
)

REQUEST_TIMEOUT = 30.0
MAX_PAGES = 10
MAX_NEW_VACANCIES = 100

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
    r"delivery\s*manager|product\s*manager|technical\s*manager|"
    r"менеджер\s+(?:it[-‑ ]?)?проект|"
    r"менеджер\s+проект|"
    r"техническ(?:ий|ого)\s+менеджер|"
    r"руководител[ья]\s+(?:it[-‑ ]?)?проект|"
    r"руководител[ья]\s+проект|"
    r"проектн(?:ый|ого)\s+офис|"
    r"program\s+management|project\s+management"
    r")",
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
    """Минимальный HTML->text без внешней зависимости BeautifulSoup."""

    BLOCK_TAGS = {
        "article", "br", "div", "h1", "h2", "h3", "li", "main",
        "p", "section", "ul", "ol",
    }

    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.h1_parts: list[str] = []
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

        self.parts.append(value)
        self.parts.append(" ")

        if self.in_h1:
            self.h1_parts.append(value)

    @property
    def title(self) -> str:
        return " ".join(self.h1_parts).strip()

    @property
    def text(self) -> str:
        raw = "".join(self.parts)
        lines = []

        for line in raw.splitlines():
            cleaned = " ".join(line.split())
            if cleaned and (not lines or cleaned != lines[-1]):
                lines.append(cleaned)

        return "\n".join(lines)


def vacancy_id_from_url(url: str) -> str | None:
    match = VACANCY_PATH_RE.match(urlparse(url).path)
    if not match:
        return None
    return match.group("id")


def is_target_title(title: str) -> bool:
    return bool(TARGET_TITLE_RE.search(title or ""))


def listing_params(page: int) -> list[tuple[str, str]]:
    params = [("profession", profession) for profession in PROFESSIONS]

    # На первой странице параметр не нужен. Для следующих используем page=N;
    # если Яндекс проигнорирует его, защита от повторяющихся ID остановит цикл.
    if page > 1:
        params.append(("page", str(page)))

    return params


def collect_listing_links(client: httpx.Client) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for page_number in range(1, MAX_PAGES + 1):
        response = client.get(
            LIST_URL,
            params=listing_params(page_number),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        parser = ListingParser()
        parser.feed(response.text)

        page_new = 0

        for url in parser.links:
            vacancy_id = vacancy_id_from_url(url)
            if not vacancy_id or vacancy_id in seen:
                continue

            seen.add(vacancy_id)
            result.append(url)
            page_new += 1

        print(
            f"[YANDEX] Страница {page_number}: "
            f"новых ссылок {page_new}, всего {len(result)}"
        )

        if page_new == 0:
            break

    return result


def fetch_vacancy(client: httpx.Client, url: str) -> tuple[str, str]:
    response = client.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    parser = VacancyTextParser()
    parser.feed(response.text)

    title = parser.title
    text = parser.text

    if not title:
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>",
            response.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if title_match:
            title = html.unescape(
                re.sub(r"<[^>]+>", " ", title_match.group(1))
            )
            title = " ".join(title.split())
            title = re.sub(
                r"^Вакансия\s+[«\"]?|[»\"]?\s+в\s+Яндексе.*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()

    if not title or len(text) < 200:
        raise RuntimeError(
            f"Не удалось разобрать карточку Яндекса: {url}"
        )

    return title, text


def save_vacancy(
    *,
    external_id: str,
    title: str,
    url: str,
    description: str,
) -> bool:
    """
    Пока не меняем схему БД: source кодируем в существующем уникальном hh_id.
    Это безопасно для старых записей HH и позволяет позже мигрировать схему.
    """
    storage_id = f"yandex:{external_id}"
    session = SessionLocal()

    try:
        existing = session.scalar(
            select(Vacancy).where(Vacancy.hh_id == storage_id)
        )
        if existing is not None:
            return False

        session.add(
            Vacancy(
                hh_id=storage_id,
                title=title,
                company="Яндекс",
                url=url,
                salary_from=None,
                salary_to=None,
                salary_currency=None,
                description=description,
                published_at=None,
                found_at=datetime.utcnow(),
                processed=False,
            )
        )
        session.commit()
        return True

    finally:
        session.close()


def main() -> int:
    print("=" * 80)
    print("YANDEX JOBS COLLECTOR")
    print("=" * 80)
    print(
        "Профессии: "
        + ", ".join(PROFESSIONS)
    )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    added = 0
    skipped_existing = 0
    skipped_title = 0
    failed = 0

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
    ) as client:
        links = collect_listing_links(client)
        print(f"[YANDEX] Найдено уникальных карточек: {len(links)}")

        for index, url in enumerate(links, start=1):
            if added >= MAX_NEW_VACANCIES:
                break

            external_id = vacancy_id_from_url(url)
            if not external_id:
                continue

            storage_id = f"yandex:{external_id}"
            session = SessionLocal()
            try:
                exists = session.scalar(
                    select(Vacancy.id).where(Vacancy.hh_id == storage_id)
                )
            finally:
                session.close()

            if exists is not None:
                skipped_existing += 1
                continue

            try:
                title, description = fetch_vacancy(client, url)

                if not is_target_title(title):
                    skipped_title += 1
                    print(f"[{index}/{len(links)}] SKIP TITLE: {title}")
                    continue

                created = save_vacancy(
                    external_id=external_id,
                    title=title,
                    url=url,
                    description=description,
                )

                if created:
                    added += 1
                    print(f"[{index}/{len(links)}] ADDED: {title}")
                else:
                    skipped_existing += 1

            except Exception as exc:
                failed += 1
                print(
                    f"[{index}/{len(links)}] ERROR {url}: "
                    f"{type(exc).__name__}: {exc}"
                )

    print("=" * 80)
    print(
        f"Добавлено: {added}; "
        f"уже были: {skipped_existing}; "
        f"нецелевые: {skipped_title}; "
        f"ошибки: {failed}"
    )
    print("=" * 80)

    return 0 if failed == 0 or added > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
