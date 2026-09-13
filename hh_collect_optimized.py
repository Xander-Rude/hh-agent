from __future__ import annotations

import multiprocessing as mp
import os
from urllib.parse import parse_qsl, urlparse

import hh_collect as base
from hh_collect_policy import (
    is_obvious_non_target_title,
    is_target_title,
    prioritize_search_queries,
)


_ORIGINAL_LOAD_PREFERENCES = base.load_preferences
_ORIGINAL_COLLECTOR_WORKER = base.collector_worker


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
    early_gate = _is_fallback_search_url(search_url)

    for selector in selectors:
        locator = page.locator(selector)
        count = locator.count()
        reliable_title = "serp-item__title" in selector

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

                if early_gate and reliable_title:
                    try:
                        title = base.clean_text(item.inner_text(timeout=1000))
                    except Exception:
                        title = ""
                    if title and is_obvious_non_target_title(title):
                        print(
                            "[SKIP SERP ROLE] "
                            f"{title} | очевидно нецелевая роль; "
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

    # Explicit environment tuning wins; only trim the old defaults.
    if "HH_DELAY_BETWEEN_PAGES" not in os.environ:
        base.DELAY_BETWEEN_PAGES = min(base.DELAY_BETWEEN_PAGES, 8.0)
    if "HH_DELAY_BETWEEN_QUERIES" not in os.environ:
        base.DELAY_BETWEEN_QUERIES = min(base.DELAY_BETWEEN_QUERIES, 10.0)


def optimized_collector_worker(heartbeat) -> None:
    install_optimizations()
    _ORIGINAL_COLLECTOR_WORKER(heartbeat)


def main() -> None:
    install_optimizations()
    # Windows multiprocessing uses spawn, so the child must install patches too.
    base.collector_worker = optimized_collector_worker
    base.run_with_watchdog()


if __name__ == "__main__":
    mp.freeze_support()
    main()
