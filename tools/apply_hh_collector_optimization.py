from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"start anchor not found: {label}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"end anchor not found: {label}")
    return text[:start_index] + new + text[end_index:]


def patch_hh_collect() -> None:
    path = ROOT / "hh_collect.py"
    text = path.read_text(encoding="utf-8")

    gate_block = r'''OBVIOUS_NON_TARGET_TITLE_PATTERNS = (
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
    """Return True only for titles that are clearly outside the target career track."""
    text = normalize_title(title)
    if not text:
        return False

    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in OBVIOUS_NON_TARGET_TITLE_PATTERNS
    )


def is_target_title(title: str) -> bool:
    """
    Conservative collector-side gate.

    Ambiguous senior titles are intentionally allowed through. We only reject
    titles that are obviously unrelated, leaving the real fit decision to the
    evaluator. This avoids losing CTO/CIO/director/head/department roles whose
    title does not contain an explicit PM/Product/Delivery keyword.
    """
    text = normalize_title(title)
    if not text:
        return False
    return not is_obvious_non_target_title(text)


def _search_query_priority(query: str) -> int:
    text = normalize_title(query)

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
    """Deduplicate queries and run the highest-value roles first without losing coverage."""
    deduped: list[str] = []
    seen: set[str] = set()

    for raw_query in queries:
        query = clean_text(str(raw_query))
        if not query:
            continue
        key = normalize_title(query)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)

    ranked = sorted(
        enumerate(deduped),
        key=lambda item: (_search_query_priority(item[1]), item[0]),
    )
    return [query for _, query in ranked]


'''

    text = replace_between(
        text,
        "def is_target_title(title: str) -> bool:\n",
        "def get_text_by_selectors(\n",
        gate_block,
        "title gate",
    )

    collect_links_block = r'''def collect_links(
    page,
    search_url: str,
    *,
    skip_obvious_non_target_titles: bool = False,
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
        reliable_title_selector = "serp-item__title" in selector

        for index in range(count):
            try:
                item = locator.nth(index)
                href = item.get_attribute("href")

                if not href:
                    continue

                if "/vacancy/" not in href:
                    continue

                if href.startswith("/"):
                    href = "https://hh.ru" + href

                href = href.split("?")[0]

                if not is_vacancy_url(href):
                    continue

                if skip_obvious_non_target_titles and reliable_title_selector:
                    try:
                        serp_title = clean_text(item.inner_text(timeout=1000))
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


'''

    text = replace_between(
        text,
        "def collect_links(\n",
        "def parse_vacancy(\n",
        collect_links_block,
        "collect_links",
    )

    text = replace_once(
        text,
        '    if not search_queries:\n        search_queries = ["Product Manager"]\n\n',
        '    if not search_queries:\n        search_queries = ["Product Manager"]\n\n'
        '    search_queries = prioritize_search_queries(list(search_queries))\n\n',
        "prioritize search queries",
    )

    text = replace_once(
        text,
        '                    links = collect_links(\n'
        '                        page=page,\n'
        '                        search_url=search_url,\n'
        '                    )\n',
        '                    links = collect_links(\n'
        '                        page=page,\n'
        '                        search_url=search_url,\n'
        '                        skip_obvious_non_target_titles=True,\n'
        '                    )\n',
        "fallback early title filter",
    )

    text = replace_once(
        text,
        '                touch_watchdog()\n'
        '                sleep_with_jitter(DELAY_BETWEEN_PAGES, 3.0)\n'
        '                touch_watchdog()\n\n'
        '            touch_watchdog()\n'
        '            sleep_with_jitter(DELAY_BETWEEN_QUERIES, 4.0)\n'
        '            touch_watchdog()\n',
        '                if page_number + 1 < MAX_PAGES_PER_QUERY:\n'
        '                    touch_watchdog()\n'
        '                    sleep_with_jitter(DELAY_BETWEEN_PAGES, 3.0)\n'
        '                    touch_watchdog()\n\n'
        '            if not stop_all and query_index < len(search_queries):\n'
        '                touch_watchdog()\n'
        '                sleep_with_jitter(DELAY_BETWEEN_QUERIES, 4.0)\n'
        '                touch_watchdog()\n',
        "avoid stacked trailing delays",
    )

    path.write_text(text, encoding="utf-8")


def patch_background_pipeline() -> None:
    path = ROOT / "background_pipeline.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import os\n\nimport httpx\n",
        "import os\nimport threading\n\nimport httpx\n",
        "threading import",
    )

    text = replace_once(
        text,
        'TRIGGERED_BY_TELEGRAM = (\n'
        '    os.getenv("HH_TRIGGERED_BY_TELEGRAM", "false").lower() == "true"\n'
        ')\n\n',
        'TRIGGERED_BY_TELEGRAM = (\n'
        '    os.getenv("HH_TRIGGERED_BY_TELEGRAM", "false").lower() == "true"\n'
        ')\n'
        'PIPELINE_HEARTBEAT_SECONDS = max(\n'
        '    1.0,\n'
        '    float(os.getenv("HH_PIPELINE_HEARTBEAT_SECONDS", "30")),\n'
        ')\n\n',
        "pipeline heartbeat interval",
    )

    helper = '''def _run_hh_collect() -> int:\n    """Run HH collector without a wall-clock cutoff and keep pipeline state fresh."""\n    stop_event = threading.Event()\n\n    def heartbeat_loop() -> None:\n        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):\n            set_stage("collect_hh")\n\n    heartbeat_thread = threading.Thread(\n        target=heartbeat_loop,\n        name="pipeline-collect-heartbeat",\n        daemon=True,\n    )\n    heartbeat_thread.start()\n\n    try:\n        # hh_collect.py has its own Playwright/Chromium watchdog. A separate\n        # supervisor wall-clock timeout can kill a healthy long collection, so\n        # do not impose one here.\n        return run_python(\n            "hh_collect.py",\n            extra_env={"HH_COLLECT_HEADLESS": "true"},\n            log_filename="collector.log",\n            timeout_seconds=None,\n        )\n    finally:\n        stop_event.set()\n        heartbeat_thread.join(timeout=2)\n\n\n'''

    text = replace_once(
        text,
        "def main() -> int:\n",
        helper + "def main() -> int:\n",
        "collector heartbeat helper",
    )

    text = replace_once(
        text,
        '                collect_code = run_python(\n'
        '                    "hh_collect.py",\n'
        '                    extra_env={"HH_COLLECT_HEADLESS": "true"},\n'
        '                    log_filename="collector.log",\n'
        '                    timeout_seconds=25 * 60,\n'
        '                )\n',
        '                collect_code = _run_hh_collect()\n',
        "remove 25 minute wall clock timeout",
    )

    path.write_text(text, encoding="utf-8")


def write_tests() -> None:
    (ROOT / "tests" / "test_hh_collect_optimization.py").write_text(
        '''from hh_collect import (\n    is_obvious_non_target_title,\n    is_target_title,\n    prioritize_search_queries,\n)\n\n\ndef test_obvious_non_target_titles_are_rejected():\n    titles = [\n        "Руководитель направления бренд-маркетинга",\n        "Руководитель направления продаж",\n        "HR Business Partner (IT, Админ, Аппарат совета директоров)",\n        "Начальник отдела образовательных программ ДПО",\n        "Директор по закупкам",\n        "Системный администратор / Помощник IT-директора",\n    ]\n\n    for title in titles:\n        assert is_obvious_non_target_title(title)\n        assert not is_target_title(title)\n\n\ndef test_ambiguous_and_senior_titles_stay_eligible():\n    titles = [\n        "Руководитель направления",\n        "Руководитель направления реализации (платформа Яндекс.Смена)",\n        "Руководитель направления маркетплейсов",\n        "Руководитель департамента",\n        "Заместитель директора",\n        "Директор",\n        "Head of Platform",\n        "CTO",\n        "CIO",\n        "Директор по информационным технологиям",\n    ]\n\n    for title in titles:\n        assert not is_obvious_non_target_title(title)\n        assert is_target_title(title)\n\n\ndef test_search_queries_are_prioritized_without_losing_coverage():\n    queries = [\n        "Руководитель направления",\n        "Project Manager",\n        "CTO",\n        "IT Business Partner",\n        "Project Manager",\n        "CIO",\n    ]\n\n    result = prioritize_search_queries(queries)\n\n    assert set(result) == {\n        "Руководитель направления",\n        "Project Manager",\n        "CTO",\n        "IT Business Partner",\n        "CIO",\n    }\n    assert result[:2] == ["CTO", "CIO"]\n    assert result.count("Project Manager") == 1\n''',
        encoding="utf-8",
    )

    (ROOT / "tests" / "test_background_pipeline_collect_heartbeat.py").write_text(
        '''import time\n\nimport background_pipeline as pipeline\n\n\ndef test_hh_collect_has_no_wall_clock_timeout_and_pulses_state(monkeypatch):\n    calls: list[str] = []\n\n    monkeypatch.setattr(pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01)\n    monkeypatch.setattr(\n        pipeline,\n        "set_stage",\n        lambda stage, **kwargs: calls.append(stage),\n    )\n\n    def fake_run_python(script_name, **kwargs):\n        assert script_name == "hh_collect.py"\n        assert kwargs["timeout_seconds"] is None\n        time.sleep(0.04)\n        return 0\n\n    monkeypatch.setattr(pipeline, "run_python", fake_run_python)\n\n    assert pipeline._run_hh_collect() == 0\n    assert "collect_hh" in calls\n''',
        encoding="utf-8",
    )


def main() -> None:
    patch_hh_collect()
    patch_background_pipeline()
    write_tests()
    print("HH collector optimization patch applied")


if __name__ == "__main__":
    main()
