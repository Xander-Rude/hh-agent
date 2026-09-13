from __future__ import annotations

import multiprocessing as mp
import os
import re
from urllib.parse import parse_qsl, urlparse

import hh_collect as base


_ORIGINAL_LOAD_PREFERENCES = base.load_preferences
_ORIGINAL_COLLECTOR_WORKER = base.collector_worker


OBVIOUS_NON_TARGET_TITLE_PATTERNS = (
    # Sales / commercial roles.
    r"\b(?:head of sales|sales director|sales manager|директор по продажам|начальник отдела продаж|руководитель отдела продаж|руководитель направления продаж)\b",
    # Pure marketing roles. Marketplace/product titles are intentionally not blocked.
    r"\b(?:бренд[- ]?маркетинг\w*|brand marketing|head of marketing|marketing director|директор по маркетингу|руководитель отдела маркетинга|руководитель направления маркетинга)\b",
    # HR roles, while HR-tech product/project titles remain eligible.
    r"\b(?:hr business partner|hrbp|head of hr|hr director|директор по персоналу|руководитель отдела персонала|руководитель направления hr|рекрутер|руководитель рекрут\w*)\b",
    # Education administration/program roles.
    r"\b(?:образовательн\w+ программ\w*|учебн\w+ программ\w*|методист|руководитель дпо)\b",
    # Clearly non-IT medical administration roles.
    r"\b(?:медицинск\w+ программ\w*|медико-социальн\w+ программ\w*|медицинский директор|главный врач|врач|медсестр\w*)\b",
    # Legal roles.
    r"\b(?:юрист|юрисконсульт|head of legal|legal counsel|руководитель юридического отдела|руководитель юридического департамента|директор юридического департамента)\b",
    # Procurement / supply roles.
    r"\b(?:head of procurement|procurement manager|директор по закупкам|руководитель закупок|руководитель отдела закупок|начальник отдела закупок|руководитель снабжен\w*)\b",
    # Clearly hands-on support/admin roles rather than IT leadership.
    r"\b(?:системный администратор|system administrator|сисадмин|помощник it-директора|помощник ит-директора)\b",
    # Obvious non-management noise that may leak into broad HH searches.
    r"\b(?:курьер|кладовщик|водитель|официант|кассир|продавец|охранник|бухгалтер)\b",
)


def is_obvious_non_target_title(title: str) -> bool:
    """Reject only titles that are clearly outside the target career track."""
    text = base.normalize_title(title)
    if not text:
        return False

    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in OBVIOUS_NON_TARGET_TITLE_PATTERNS
    )


def is_target_title(title: str) -> bool:
    """
    Conservative title gate.

    Ambiguous senior titles intentionally pass. The evaluator, not the
    collector, decides whether a generic director/head/department role is a
    real fit. This avoids losing CTO/CIO/director-level vacancies just because
    the title does not contain an explicit PM/Product/Delivery keyword.
    """
    text = base.normalize_title(title)
    if not text:
        return False
    return not is_obvious_non_target_title(text)


def _search_query_priority(query: str) -> int:
    text = base.normalize_title(query)

    c_level_markers = (
        "cto",
        "cio",
        "chief technology officer",
        "chief information officer",
        "технический директор",
        "it director",
        "it-директор",
        "it директор",
        "ит-директор",
        "ит директор",
        "директор по ит",
        "директор по информационным технологиям",
        "директор информационных технологий",
        "директор по цифровой трансформации",
        "директор цифровой трансформации",
    )
    if any(marker in text for marker in c_level_markers):
        return 0

    core_management_markers = (
        "pmo",
        "project office",
        "project manager",
        "program manager",
        "programme manager",
        "delivery",
        "portfolio",
        "руководитель проекта",
        "руководитель проектов",
        "руководитель программы",
        "проектного офиса",
        "портфел",
        "руководитель разработки",
        "head of engineering",
        "engineering manager",
        "it lead",
        "ит лидер",
    )
    if any(marker in text for marker in core_management_markers):
        return 1

    return 2


def prioritize_search_queries(queries: list[str]) -> list[str]:
    """Deduplicate and reorder queries without reducing search coverage."""
    deduped: list[str] = []
    seen: set[str] = set()

    for raw_query in queries:
        query = base.clean_text(str(raw_query))
        if not query:
            continue
        key = base.normalize_title(query)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)

    ranked = sorted(
        enumerate(deduped),
        key=lambda item: (_search_query_priority(item[1]), item[0]),
    )
    return [query for _, query in ranked]


def load_preferences_optimized() -> dict:
    preferences = _ORIGINAL_LOAD_PREFERENCES()
    if not isinstance(preferences, dict):
        return preferences

    result = dict(preferences)
    roles = result.get("target_roles")
    if isinstance(roles, (list, tuple)):
        result["target_roles"] = prioritize_search_queries(list(roles))
    return result


def _is_fallback_search_url(search_url: str) -> bool:
    """Identify URLs produced by hh_collect.build_search_url()."""
    try:
        params = dict(parse_qsl(urlparse(search_url).query, keep_blank_values=True))
    except Exception:
        return False

    return (
        params.get("search_field") == "name"
        and "text" in params
        and "period" in params
    )


def collect_links_optimized(page, search_url: str) -> list[str]:
    """Collect SERP links and skip only obvious junk before opening vacancy pages."""
    base.goto_or_stop(
        page,
        search_url,
        context_label="Ошибка на странице поиска HH.",
    )
    page.wait_for_timeout(base.PAGE_LOAD_WAIT_MS)
    base.touch_watchdog()

    selectors = [
        'a[data-qa="serp-item__title"]',
        'a[href*="/vacancy/"]',
    ]
    links: list[str] = []
    apply_early_gate = _is_fallback_search_url(search_url)

    for selector in selectors:
        locator = page.locator(selector)
        count = locator.count()
        reliable_title_selector = "serp-item__title" in selector

        for index in range(count):
            try:
                item = locator.nth(index)
                href = item.get_attribute("href")
                if not href or "/vacancy/" not in href:
                    continue

                if href.startswith("/"):
                    href = "https://hh.ru" + href
                href = href.split("?", 1)[0]

                if not base.is_vacancy_url(href):
                    continue

                if apply_early_gate and reliable_title_selector:
                    try:
                        serp_title = base.clean_text(item.inner_text(timeout=1000))
                    except Exception:
                        serp_title = ""

                    if serp_title and is_obvious_non_target_title(serp_title):
                        print(
                            "[SKIP SERP ROLE] "
                            f"{serp_title} | очевидно нецелевая роль; "
                            "карточку вакансии не открываю"
                        )
                        continue

                if href not in links:
                    links.append(href)
            except Exception:
                continue

        if links:
            break

    return links


def install_optimizations() -> None:
    base.load_preferences = load_preferences_optimized
    base.is_target_title = is_target_title
    base.collect_links = collect_links_optimized

    # Keep user-provided pacing untouched. Only trim conservative defaults to
    # avoid spending many minutes in stacked sleeps across 36 fallback queries.
    if "HH_DELAY_BETWEEN_PAGES" not in os.environ:
        base.DELAY_BETWEEN_PAGES = min(base.DELAY_BETWEEN_PAGES, 8.0)
    if "HH_DELAY_BETWEEN_QUERIES" not in os.environ:
        base.DELAY_BETWEEN_QUERIES = min(base.DELAY_BETWEEN_QUERIES, 10.0)


def optimized_collector_worker(heartbeat) -> None:
    """Install the same optimizations inside the multiprocessing worker."""
    install_optimizations()
    _ORIGINAL_COLLECTOR_WORKER(heartbeat)


def main() -> None:
    install_optimizations()

    # hh_collect.run_with_watchdog() uses this global as the spawn target.
    # Replacing it preserves the existing watchdog while ensuring the child
    # process installs the optimization layer too.
    base.collector_worker = optimized_collector_worker
    base.run_with_watchdog()


if __name__ == "__main__":
    mp.freeze_support()
    main()
