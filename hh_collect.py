import multiprocessing as mp
import os
import re
import random
import signal
import subprocess
import time
import traceback
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from dotenv import load_dotenv
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from sqlalchemy import select

from app.db import SessionLocal, Vacancy
from app.preferences import load_preferences


load_dotenv()


PROFILE_DIR = "browser-profile"


COLLECT_HEADLESS = (
    os.getenv(
        "HH_COLLECT_HEADLESS",
        "false",
    ).lower()
    == "true"
)

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
).strip()

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

NAVIGATION_TIMEOUT_MS = int(
    os.getenv(
        "HH_COLLECT_NAVIGATION_TIMEOUT_MS",
        "30000",
    )
)

WATCHDOG_SECONDS = int(
    os.getenv(
        "HH_COLLECT_WATCHDOG_SECONDS",
        "120",
    )
)

WATCHDOG_POLL_SECONDS = 2.0
_WATCHDOG_HEARTBEAT = None

SEARCH_BASE_URL = "https://hh.ru/search/vacancy"
RESUMES_URL = "https://hh.ru/applicant/resumes"
HOME_URL = "https://hh.ru/"

HH_AREA = 1

MAX_PAGES_PER_QUERY = 2
MAX_RECOMMENDATION_PAGES = int(os.getenv("HH_RECOMMENDATION_PAGES", "3"))
MAX_NEW_VACANCIES_TOTAL = 100
MAX_VACANCIES_PER_PAGE = 30

FRESHNESS_DAYS = 3

PAGE_LOAD_WAIT_MS = 3500
VACANCY_LOAD_WAIT_MS = 2200
DELAY_BETWEEN_VACANCIES = 3.0
DELAY_BETWEEN_PAGES = 5.0
DELAY_BETWEEN_QUERIES = 8.0


class CollectorFatalError(RuntimeError):
    pass



def sleep_with_jitter(base_seconds: float, jitter_seconds: float) -> None:
    delay = max(
        0.0,
        base_seconds + random.uniform(
            -jitter_seconds,
            jitter_seconds,
        ),
    )
    time.sleep(delay)


def notify_telegram(
    message: str,
) -> None:
    """
    Best-effort alert. A Telegram failure must never hide the original
    collector problem.
    """
    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        print(
            "[TG WARN] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
            "не настроены; уведомление не отправлено."
        )
        return

    text = (
        "🚨 HH Agent: collector остановлен\n\n"
        + message.strip()
    )

    try:
        response = httpx.post(
            (
                "https://api.telegram.org/bot"
                f"{TELEGRAM_BOT_TOKEN}/sendMessage"
            ),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text[:4000],
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )

        response.raise_for_status()

        print(
            "[TG] Аварийное уведомление отправлено."
        )

    except Exception as exc:
        print(
            "[TG WARN] Не удалось отправить уведомление: "
            f"{type(exc).__name__}: {exc}"
        )


def touch_watchdog() -> None:
    heartbeat = _WATCHDOG_HEARTBEAT

    if heartbeat is None:
        return

    try:
        heartbeat.value = time.time()
    except Exception:
        # Watchdog must never break collection itself.
        pass


def kill_process_tree(pid: int) -> None:
    """Best-effort hard stop for a stuck collector worker and Chromium tree."""
    if pid <= 0:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            return
        except Exception:
            pass

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def collector_worker(heartbeat) -> None:
    global _WATCHDOG_HEARTBEAT

    _WATCHDOG_HEARTBEAT = heartbeat
    touch_watchdog()

    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        try:
            notify_telegram(
                "Collector завершился с ошибкой.\n"
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        raise


def run_with_watchdog() -> None:
    """
    Run the real collector in a child process.

    Playwright/Chromium can occasionally freeze below Python's own timeout
    machinery. In that case a timeout inside page.goto() never gets a chance
    to raise. The parent process therefore watches a shared heartbeat and can
    kill the entire worker+Chromium process tree.

    On watchdog timeout we intentionally return exit code 0: all vacancies
    committed before the freeze are already safe in SQLite, and the outer
    pipeline should continue to process_vacancies.py instead of staying locked
    forever on the collect stage.
    """
    if WATCHDOG_SECONDS <= 0:
        main()
        return

    ctx = mp.get_context("spawn")
    heartbeat = ctx.Value("d", time.time())
    worker = ctx.Process(
        target=collector_worker,
        args=(heartbeat,),
        name="hh-collector-worker",
    )

    worker.start()

    while worker.is_alive():
        worker.join(
            timeout=WATCHDOG_POLL_SECONDS
        )

        if not worker.is_alive():
            break

        try:
            heartbeat_age = (
                time.time()
                - heartbeat.value
            )
        except Exception:
            heartbeat_age = 0.0

        if heartbeat_age <= WATCHDOG_SECONDS:
            continue

        message = (
            "Collector не подаёт heartbeat "
            f"более {WATCHDOG_SECONDS} сек. "
            "Вероятно, завис Playwright/Chromium. "
            "Процесс collector будет принудительно остановлен, "
            "а pipeline продолжит обработку уже собранных вакансий."
        )

        print(
            f"[WATCHDOG] {message}",
            flush=True,
        )

        try:
            notify_telegram(message)
        except Exception:
            pass

        kill_process_tree(
            worker.pid or 0
        )

        worker.join(timeout=10)

        if worker.is_alive():
            try:
                worker.terminate()
            except Exception:
                pass
            worker.join(timeout=5)

        print(
            "[WATCHDOG] Зависший collector остановлен. "
            "Частичный результат сохранён; pipeline может переходить "
            "к process_vacancies.py.",
            flush=True,
        )
        return

    exit_code = worker.exitcode

    if exit_code not in (0, None):
        raise SystemExit(exit_code)


def get_page_text(
    page,
) -> str:
    try:
        return (
            page.locator("body")
            .inner_text(timeout=3000)
            .lower()
        )
    except Exception:
        return ""


def detect_hh_block(
    page,
) -> str | None:
    """
    Detect only strong anti-bot / access-block signals.
    Keep markers conservative to avoid matching ordinary vacancy text.
    """
    try:
        current_url = (
            page.url
            or ""
        ).lower()
    except Exception:
        current_url = ""

    text = get_page_text(page)

    url_markers = [
        "captcha",
        "challenge",
    ]

    for marker in url_markers:
        if marker in current_url:
            return (
                f"HH открыл служебную страницу: {current_url}"
            )

    text_markers = [
        "подтвердите, что вы не робот",
        "проверка, что вы не робот",
        "введите код с картинки",
        "слишком много запросов",
        "доступ временно ограничен",
        "доступ ограничен",
        "подозрительная активность",
        "verify you are human",
        "security check",
        "captcha",
    ]

    for marker in text_markers:
        if marker in text:
            return (
                f"HH показал антибот/блокировку: {marker}"
            )

    return None


def goto_or_stop(
    page,
    url: str,
    *,
    context_label: str,
) -> None:
    touch_watchdog()

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        touch_watchdog()

    except PlaywrightTimeoutError as exc:
        raise CollectorFatalError(
            f"{context_label}\n"
            f"HH не загрузил страницу за "
            f"{NAVIGATION_TIMEOUT_MS // 1000} сек.\n"
            f"URL: {url}\n"
            "Collector остановлен, чтобы не продолжать "
            "работу в неизвестном состоянии."
        ) from exc

    except Exception as exc:
        raise CollectorFatalError(
            f"{context_label}\n"
            f"Ошибка открытия HH: "
            f"{type(exc).__name__}: {exc}\n"
            f"URL: {url}"
        ) from exc

    touch_watchdog()

    block_reason = detect_hh_block(
        page
    )

    touch_watchdog()

    if block_reason:
        raise CollectorFatalError(
            f"{context_label}\n"
            f"{block_reason}\n"
            f"URL: {url}"
        )



def is_vacancy_url(
    url: str,
) -> bool:
    """
    Принимает только настоящие ссылки на вакансии вида:
    https://hh.ru/vacancy/136456329

    Отсекает служебные ссылки вроде:
    https://hh.ru/search/vacancy/map
    """
    try:
        parsed = urlparse(
            url
        )

        path = (
            parsed.path
            or ""
        ).rstrip("/")

        return bool(
            re.fullmatch(
                r"/vacancy/\d+",
                path,
            )
        )

    except Exception:
        return False



def extract_hh_id(url: str) -> str:
    path = urlparse(url).path

    match = re.search(
        r"/vacancy/(\d+)",
        path,
    )

    if not match:
        raise ValueError(
            f"Не удалось определить hh_id из URL: {url}"
        )

    return match.group(1)


def clean_text(text: str | None) -> str:
    if not text:
        return ""

    return " ".join(text.split())


def normalize_title(text: str | None) -> str:
    value = clean_text(text).lower()
    value = value.replace("ё", "е")
    value = value.replace("—", "-")
    value = value.replace("–", "-")
    return value


def is_target_title(title: str) -> bool:
    """
    Cheap collector-side role gate.

    We intentionally use an allow-list of management/product/project
    role patterns instead of trying to blacklist every possible junk job.
    This prevents obvious vacancies such as couriers, drivers, waiters,
    warehouse workers, etc. from ever reaching SQLite/LLM.
    """
    text = normalize_title(title)

    if not text:
        return False

    exact_phrases = [
        # Project / programme / delivery
        "project manager",
        "senior project manager",
        "technical project manager",
        "it project manager",
        "program manager",
        "programme manager",
        "delivery manager",
        "delivery lead",
        "project lead",
        "руководитель проекта",
        "руководитель проектов",
        "руководитель it-проекта",
        "руководитель it-проектов",
        "руководитель ит-проекта",
        "руководитель ит-проектов",
        "менеджер проекта",
        "менеджер проектов",
        "ведущий менеджер проектов",
        "технический руководитель проекта",
        "технический руководитель проектов",

        # PMO / project office / portfolio
        "pmo",
        "project office",
        "руководитель проектного офиса",
        "начальник проектного офиса",
        "директор проектного офиса",
        "руководитель офиса управления проектами",
        "руководитель портфеля проектов",
        "portfolio manager",

        # Product
        "product manager",
        "senior product manager",
        "technical product manager",
        "product owner",
        "product lead",
        "head of product",
        "руководитель продукта",
        "руководитель продукт",
        "менеджер продукта",
        "менеджер по продукту",
        "продакт менеджер",
        "продакт-менеджер",
        "владелец продукта",

        # Broader senior IT-management titles that are still relevant
        "руководитель it-направления",
        "руководитель ит-направления",
        "руководитель направления it",
        "руководитель направления ит",
        "руководитель разработки",
        "head of delivery",
        "head of projects",
        "head of project",
    ]

    return any(
        phrase in text
        for phrase in exact_phrases
    )



def get_text_by_selectors(
    page,
    selectors: list[str],
) -> str:
    for selector in selectors:
        locator = page.locator(selector)

        if locator.count() == 0:
            continue

        try:
            text = locator.first.inner_text(
                timeout=3000
            )

            text = clean_text(text)

            if text:
                return text

        except Exception:
            continue

    return ""


def parse_salary(
    text: str,
) -> tuple[
    int | None,
    int | None,
    str | None,
]:
    if not text:
        return None, None, None

    normalized = (
        text
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )

    numbers = re.findall(
        r"\d[\d\s]*",
        normalized,
    )

    values: list[int] = []

    for number in numbers:
        digits = re.sub(
            r"\D",
            "",
            number,
        )

        if digits:
            values.append(
                int(digits)
            )

    currency = None
    lower = normalized.lower()

    if (
        "₽" in normalized
        or "руб" in lower
    ):
        currency = "RUB"

    elif (
        "$" in normalized
        or "usd" in lower
    ):
        currency = "USD"

    elif (
        "€" in normalized
        or "eur" in lower
    ):
        currency = "EUR"

    salary_from = None
    salary_to = None

    if len(values) >= 2:
        salary_from = values[0]
        salary_to = values[1]

    elif len(values) == 1:
        if "от " in lower:
            salary_from = values[0]

        elif "до " in lower:
            salary_to = values[0]

        else:
            salary_from = values[0]

    return (
        salary_from,
        salary_to,
        currency,
    )


def vacancy_exists(
    session,
    hh_id: str,
) -> bool:
    stmt = (
        select(Vacancy.id)
        .where(
            Vacancy.hh_id == hh_id
        )
    )

    return (
        session.execute(
            stmt
        ).first()
        is not None
    )


def save_vacancy(
    hh_id: str,
    title: str,
    company: str,
    url: str,
    salary_text: str,
    description: str,
) -> bool:
    session = SessionLocal()

    try:
        if vacancy_exists(
            session,
            hh_id,
        ):
            print(
                f"[SKIP] {hh_id} уже есть в базе"
            )

            return False

        (
            salary_from,
            salary_to,
            salary_currency,
        ) = parse_salary(
            salary_text
        )

        vacancy = Vacancy(
            hh_id=hh_id,
            title=title,
            company=company or None,
            url=url,
            salary_from=salary_from,
            salary_to=salary_to,
            salary_currency=salary_currency,
            description=description,
            published_at=None,
            found_at=datetime.now(UTC),
            processed=False,
        )

        session.add(vacancy)
        session.commit()

        print(
            f"[SAVE] "
            f"{hh_id} | "
            f"{title} | "
            f"{company or '-'}"
        )

        return True

    finally:
        session.close()


def build_search_url(
    query: str,
    page_number: int,
) -> str:
    params = {
        "text": query,
        "area": HH_AREA,
        "period": FRESHNESS_DAYS,
        # Do not search query words in company/description.
        # This dramatically reduces irrelevant SERP results.
        "search_field": "name",
        "page": page_number,
        "per_page": MAX_VACANCIES_PER_PAGE,
    }

    return (
        SEARCH_BASE_URL
        + "?"
        + urlencode(params)
    )


def collect_links(
    page,
    search_url: str,
) -> list[str]:
    goto_or_stop(
        page,
        search_url,
        context_label="Ошибка на странице поиска HH.",
    )

    page.wait_for_timeout(
        PAGE_LOAD_WAIT_MS
    )

    selectors = [
        'a[data-qa="serp-item__title"]',
        'a[href*="/vacancy/"]',
    ]

    links: list[str] = []

    for selector in selectors:
        locator = page.locator(selector)
        count = locator.count()

        for index in range(count):
            try:
                href = locator.nth(
                    index
                ).get_attribute(
                    "href"
                )

                if not href:
                    continue

                if "/vacancy/" not in href:
                    continue

                if href.startswith("/"):
                    href = (
                        "https://hh.ru"
                        + href
                    )

                href = href.split(
                    "?"
                )[0]

                if not is_vacancy_url(
                    href
                ):
                    continue

                if href not in links:
                    links.append(
                        href
                    )

            except Exception:
                continue

        if links:
            break

    return links


def parse_vacancy(
    page,
    url: str,
) -> dict:
    goto_or_stop(
        page,
        url,
        context_label="Ошибка при открытии вакансии.",
    )

    page.wait_for_timeout(
        VACANCY_LOAD_WAIT_MS
    )

    title = get_text_by_selectors(
        page,
        [
            'h1[data-qa="vacancy-title"]',
            "h1",
        ],
    )

    company = get_text_by_selectors(
        page,
        [
            '[data-qa="vacancy-company-name"]',
            '[data-qa="bloko-header-2"]',
        ],
    )

    salary = get_text_by_selectors(
        page,
        [
            '[data-qa="vacancy-salary"]',
            '[data-qa="vacancy-salary-compensation-type-net"]',
        ],
    )

    description = get_text_by_selectors(
        page,
        [
            '[data-qa="vacancy-description"]',
            ".vacancy-description",
        ],
    )

    return {
        "title": title,
        "company": company,
        "salary": salary,
        "description": description,
    }



def absolute_hh_url(href: str | None) -> str:
    """Normalize an HH href and keep query parameters used by recommendations."""
    href = (href or "").strip()

    if not href:
        return ""

    if href.startswith("/"):
        return "https://hh.ru" + href

    if href.startswith("https://hh.ru/"):
        return href

    if href.startswith("http://hh.ru/"):
        return "https://hh.ru/" + href.split("http://hh.ru/", 1)[1]

    return ""


def paginated_url(url: str, page_number: int) -> str:
    """Change only pagination while preserving HH recommendation parameters."""
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["page"] = str(page_number)
    params["per_page"] = str(MAX_VACANCIES_PER_PAGE)

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or "hh.ru",
            parsed.path,
            parsed.params,
            urlencode(params),
            parsed.fragment,
        )
    )


def _recommendation_link_score(text: str, href: str, data_qa: str) -> int:
    """Score links that look like resume-specific HH recommendation feeds."""
    normalized = clean_text(text).lower().replace("ё", "е")
    href_lower = href.lower()
    qa_lower = data_qa.lower()
    score = 0

    if "ваканс" in normalized:
        score += 2

    if "перейти" in normalized and "ваканс" in normalized:
        score += 6

    if "подходящ" in normalized and "ваканс" in normalized:
        score += 6

    if re.search(r"\\d[\\d\\s]*\\s+ваканс", normalized):
        score += 5

    if "recommend" in qa_lower or "vacanc" in qa_lower:
        score += 2

    if "/search/vacancy" in href_lower:
        score += 2

    if "resume" in href_lower:
        score += 3

    if any(
        marker in href_lower
        for marker in (
            "recommended",
            "recommendation",
            "from=resume",
            "hhtmfrom=resume",
        )
    ):
        score += 3

    # Plain top-navigation link \"Вакансии\" is not a recommendation feed.
    if normalized in {"вакансии", "поиск вакансий"}:
        score -= 8

    return score


def discover_recommendation_urls(page) -> list[str]:
    """
    Discover recommendation feeds from the authenticated HH UI.

    We intentionally do not hard-code HH's internal recommendation endpoint:
    the account UI already contains the canonical links for each resume.
    """
    goto_or_stop(
        page,
        RESUMES_URL,
        context_label="Не удалось открыть страницу «Мои резюме».",
    )
    page.wait_for_timeout(PAGE_LOAD_WAIT_MS)
    touch_watchdog()

    candidates: list[tuple[int, str, str]] = []
    anchors = page.locator("a[href]")

    try:
        count = anchors.count()
    except Exception:
        count = 0

    for index in range(count):
        item = anchors.nth(index)

        try:
            href = absolute_hh_url(item.get_attribute("href"))

            if not href:
                continue

            parsed = urlparse(href)
            if parsed.netloc not in {"hh.ru", "www.hh.ru"}:
                continue

            # Recommendation feeds ultimately lead to vacancy search/results.
            if "/search/vacancy" not in parsed.path:
                continue

            try:
                text = clean_text(item.inner_text(timeout=1000))
            except Exception:
                text = clean_text(item.get_attribute("aria-label"))

            data_qa = clean_text(item.get_attribute("data-qa"))
            score = _recommendation_link_score(text, href, data_qa)

            if score < 4:
                continue

            candidates.append((score, href, text or data_qa or "без подписи"))

        except Exception:
            continue

    # Highest-confidence candidates first; remove exact duplicates.
    candidates.sort(key=lambda item: item[0], reverse=True)
    result: list[str] = []

    for score, href, label in candidates:
        if href in result:
            continue

        result.append(href)
        print(
            f"[RECOMMENDATION] score={score} | "
            f"{label[:100]} | {href}"
        )

    if result:
        return result

    print(
        "[WARN] На странице «Мои резюме» не удалось получить "
        "ссылки на подходящие вакансии. Пробую блок «Для вас» "
        "на главной странице HH."
    )

    goto_or_stop(
        page,
        HOME_URL,
        context_label="Не удалось открыть главную страницу HH.",
    )
    page.wait_for_timeout(PAGE_LOAD_WAIT_MS)
    touch_watchdog()

    anchors = page.locator("a[href]")

    try:
        count = anchors.count()
    except Exception:
        count = 0

    homepage_candidates: list[tuple[int, str, str]] = []

    for index in range(count):
        item = anchors.nth(index)
        try:
            href = absolute_hh_url(item.get_attribute("href"))
            if not href or "/search/vacancy" not in urlparse(href).path:
                continue

            try:
                text = clean_text(item.inner_text(timeout=1000))
            except Exception:
                text = clean_text(item.get_attribute("aria-label"))

            data_qa = clean_text(item.get_attribute("data-qa"))
            score = _recommendation_link_score(text, href, data_qa)

            if score < 4:
                continue

            homepage_candidates.append((score, href, text or data_qa or "без подписи"))
        except Exception:
            continue

    homepage_candidates.sort(key=lambda item: item[0], reverse=True)

    for score, href, label in homepage_candidates:
        if href in result:
            continue
        result.append(href)
        print(
            f"[RECOMMENDATION HOME] score={score} | "
            f"{label[:100]} | {href}"
        )

    return result


def process_vacancy_links(
    *,
    page,
    links: list[str],
    source_label: str,
    apply_role_gate: bool,
    seen_this_run: set[str],
    saved_total: int,
) -> tuple[int, bool]:
    """Process a batch and return (saved_total, hit_global_limit)."""
    new_links: list[str] = []

    for link in links:
        # collect_links strips tracking query parameters from vacancy URLs;
        # normalize here as well in case another source did not.
        normalized = link.split("?", 1)[0]

        if normalized in seen_this_run:
            continue

        seen_this_run.add(normalized)
        new_links.append(normalized)

    print(f"Ссылок на странице: {len(links)}")
    print(f"Уникальных в этом проходе: {len(new_links)}")

    if not new_links:
        print("[INFO] Все вакансии на странице уже встречались.")
        return saved_total, False

    for index, url in enumerate(new_links, start=1):
        if saved_total >= MAX_NEW_VACANCIES_TOTAL:
            return saved_total, True

        print()
        touch_watchdog()
        print(
            f"[VACANCY {index}/{len(new_links)}] "
            f"[{source_label}] {url}"
        )

        if not is_vacancy_url(url):
            print(f"[SKIP URL] Служебная ссылка HH: {url}")
            continue

        try:
            hh_id = extract_hh_id(url)
            session = SessionLocal()

            try:
                exists = vacancy_exists(session, hh_id)
            finally:
                session.close()

            if exists:
                print(f"[SKIP] {hh_id} уже есть в базе")
                continue

            touch_watchdog()
            data = parse_vacancy(page=page, url=url)
            touch_watchdog()

            if not data["title"]:
                print("[WARN] Не удалось получить название вакансии")
                continue

            if apply_role_gate and not is_target_title(data["title"]):
                print(
                    "[SKIP ROLE] "
                    f"{data['title']} | "
                    "название не похоже на целевую PM/Product/Delivery роль"
                )
                continue

            if not apply_role_gate:
                print(
                    "[HH RECOMMENDATION] "
                    f"{data['title']} | title-gate пропущен; "
                    "решение оставлено следующему этапу evaluator"
                )

            if not data["description"]:
                print("[WARN] Не удалось получить описание вакансии")
                continue

            touch_watchdog()
            was_saved = save_vacancy(
                hh_id=hh_id,
                title=data["title"],
                company=data["company"],
                url=url,
                salary_text=data["salary"],
                description=data["description"],
            )
            touch_watchdog()

            if was_saved:
                saved_total += 1
                print(
                    f"[TOTAL] {saved_total}/"
                    f"{MAX_NEW_VACANCIES_TOTAL}"
                )

            touch_watchdog()
            sleep_with_jitter(DELAY_BETWEEN_VACANCIES, 1.0)
            touch_watchdog()

        except CollectorFatalError as exc:
            raise CollectorFatalError(
                f"{exc}\n"
                f"Источник: {source_label}\n"
                f"Вакансия: {index}/{len(new_links)}"
            ) from exc

        except Exception as exc:
            raise CollectorFatalError(
                "Непредвиденная ошибка при обработке вакансии.\n"
                f"Источник: {source_label}\n"
                f"URL: {url}\n"
                f"{type(exc).__name__}: {exc}"
            ) from exc

    return saved_total, False

def main() -> None:
    touch_watchdog()
    preferences = load_preferences()

    search_queries = preferences.get(
        "target_roles",
        [],
    )

    if not search_queries:
        search_queries = ["Product Manager"]

    print("Запускаю HH collector...")
    print("Режим: HH recommendations -> fallback search")
    print(f"Поисковых запросов fallback: {len(search_queries)}")
    print(f"Страниц на fallback-запрос: {MAX_PAGES_PER_QUERY}")
    print(f"Страниц рекомендаций на резюме: {MAX_RECOMMENDATION_PAGES}")
    print(f"Свежесть fallback-поиска: последние {FRESHNESS_DAYS} дня/дней")
    print(f"Общий лимит новых вакансий: {MAX_NEW_VACANCIES_TOTAL}")

    saved_total = 0
    seen_this_run: set[str] = set()

    with sync_playwright() as p:
        touch_watchdog()
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=COLLECT_HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        touch_watchdog()
        page = context.pages[0]
        touch_watchdog()

        stop_all = False

        # ------------------------------------------------------------------
        # 1. PRIMARY SOURCE: HH's personalized recommendations.
        # ------------------------------------------------------------------
        print()
        print("=" * 80)
        print("[SOURCE 1] ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ HH")
        print("=" * 80)

        recommendation_urls = discover_recommendation_urls(page)
        touch_watchdog()

        if not recommendation_urls:
            print(
                "[WARN] HH не отдал ссылку на персональную подборку. "
                "Перехожу к fallback-поиску по target_roles."
            )

        for feed_index, recommendation_url in enumerate(
            recommendation_urls,
            start=1,
        ):
            if stop_all:
                break

            print()
            print(
                f"[RECOMMENDATION FEED {feed_index}/"
                f"{len(recommendation_urls)}] {recommendation_url}"
            )

            for page_number in range(MAX_RECOMMENDATION_PAGES):
                if saved_total >= MAX_NEW_VACANCIES_TOTAL:
                    stop_all = True
                    break

                feed_page_url = paginated_url(
                    recommendation_url,
                    page_number,
                )

                print()
                print(
                    f"[RECOMMENDATION PAGE {page_number + 1}/"
                    f"{MAX_RECOMMENDATION_PAGES}]"
                )

                try:
                    links = collect_links(
                        page=page,
                        search_url=feed_page_url,
                    )
                    touch_watchdog()
                except CollectorFatalError:
                    raise
                except Exception as exc:
                    raise CollectorFatalError(
                        "Не удалось прочитать персональную подборку HH.\n"
                        f"Feed: {recommendation_url}\n"
                        f"Page: {page_number + 1}\n"
                        f"{type(exc).__name__}: {exc}"
                    ) from exc

                if not links:
                    print(
                        "[INFO] В персональной подборке на этой странице "
                        "вакансий нет."
                    )
                    break

                saved_total, hit_limit = process_vacancy_links(
                    page=page,
                    links=links,
                    source_label="HH_RECOMMENDATION",
                    apply_role_gate=False,
                    seen_this_run=seen_this_run,
                    saved_total=saved_total,
                )

                if hit_limit:
                    stop_all = True
                    break

                touch_watchdog()
                sleep_with_jitter(DELAY_BETWEEN_PAGES, 1.5)
                touch_watchdog()

        # ------------------------------------------------------------------
        # 2. FALLBACK SOURCE: our old role-based search.
        # ------------------------------------------------------------------
        if not stop_all:
            print()
            print("=" * 80)
            print("[SOURCE 2] FALLBACK-ПОИСК ПО TARGET_ROLES")
            print("=" * 80)

        for query_index, query in enumerate(search_queries, start=1):
            if stop_all:
                break

            touch_watchdog()
            print()
            print(
                f"[SEARCH {query_index}/{len(search_queries)}] {query}"
            )
            print("=" * 80)

            for page_number in range(MAX_PAGES_PER_QUERY):
                if saved_total >= MAX_NEW_VACANCIES_TOTAL:
                    stop_all = True
                    break

                print()
                print(
                    f"[PAGE {page_number + 1}/{MAX_PAGES_PER_QUERY}]"
                )

                search_url = build_search_url(
                    query=str(query),
                    page_number=page_number,
                )
                touch_watchdog()

                try:
                    links = collect_links(
                        page=page,
                        search_url=search_url,
                    )
                    touch_watchdog()
                except CollectorFatalError:
                    raise
                except Exception as exc:
                    raise CollectorFatalError(
                        "Не удалось прочитать страницу поиска.\n"
                        f"Query: {query}\n"
                        f"Page: {page_number + 1}\n"
                        f"{type(exc).__name__}: {exc}"
                    ) from exc

                if not links:
                    print("[INFO] На странице не найдено вакансий.")
                    break

                saved_total, hit_limit = process_vacancy_links(
                    page=page,
                    links=links,
                    source_label=f"SEARCH:{query}",
                    apply_role_gate=True,
                    seen_this_run=seen_this_run,
                    saved_total=saved_total,
                )

                if hit_limit:
                    stop_all = True
                    break

                touch_watchdog()
                sleep_with_jitter(DELAY_BETWEEN_PAGES, 1.5)
                touch_watchdog()

            touch_watchdog()
            sleep_with_jitter(DELAY_BETWEEN_QUERIES, 2.0)
            touch_watchdog()

        touch_watchdog()
        context.close()
        touch_watchdog()

    print()
    print("=" * 80)
    print("Готово.")
    print(f"Новых вакансий сохранено: {saved_total}")

    if saved_total >= MAX_NEW_VACANCIES_TOTAL:
        print(
            "Достигнут общий лимит "
            f"{MAX_NEW_VACANCIES_TOTAL} новых вакансий."
        )

    print("=" * 80)


if __name__ == "__main__":
    mp.freeze_support()
    run_with_watchdog()
