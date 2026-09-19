from __future__ import annotations

import re

import hh_collect as base


DELIVERY_SEARCH_ALIASES = (
    "Delivery Manager",
    "IT Delivery Manager",
    "Delivery Lead",
    "Technical Delivery Manager",
)


OBVIOUS_NON_TARGET_TITLE_PATTERNS = (
    r"\b(?:head of sales|sales director|sales manager|директор по продажам|начальник отдела продаж|руководитель отдела продаж|руководитель направления продаж)\b",
    r"\b(?:бренд[- ]?маркетинг\w*|brand marketing|head of marketing|marketing director|директор по маркетингу|руководитель отдела маркетинга|руководитель направления маркетинга)\b",
    r"\b(?:hr business partner|hrbp|head of hr|hr director|директор по персоналу|руководитель отдела персонала|руководитель направления hr|рекрутер|руководитель рекрут\w*)\b",
    r"\b(?:образовательн\w+ программ\w*|учебн\w+ программ\w*|методист|руководитель дпо)\b",
    r"\b(?:медицинск\w+ программ\w*|медико-социальн\w+ программ\w*|медицинский директор|главный врач|врач|медсестр\w*)\b",
    r"\b(?:юрист|юрисконсульт|head of legal|legal counsel|руководитель юридического отдела|руководитель юридического департамента|директор юридического департамента)\b",
    r"\b(?:head of procurement|procurement manager|директор по закупкам|руководитель закупок|руководитель отдела закупок|начальник отдела закупок|руководитель снабжен\w*)\b",
    r"\b(?:системный администратор|system administrator|сисадмин|помощник it-директора|помощник ит-директора)\b",
    r"\b(?:курьер|кладовщик|водитель|официант|кассир|продавец|охранник|бухгалтер)\b",
)


def is_obvious_non_target_title(title: str) -> bool:
    text = base.normalize_title(title)
    if not text:
        return False
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in OBVIOUS_NON_TARGET_TITLE_PATTERNS
    )


def is_target_title(title: str) -> bool:
    """Allow ambiguous senior roles; reject only clearly unrelated titles."""
    text = base.normalize_title(title)
    if not text:
        return False
    return not is_obvious_non_target_title(text)


def _search_query_priority(query: str) -> int:
    text = base.normalize_title(query)

    project_markers = (
        "project manager", "senior project manager", "technical project manager",
        "it project manager", "руководитель проекта", "руководитель проектов",
        "руководитель it-проекта", "руководитель it проекта",
        "руководитель it-проектов", "руководитель it проектов",
        "руководитель ит-проекта", "руководитель ит проекта",
        "руководитель ит-проектов", "руководитель ит проектов",
        "менеджер проектов", "менеджер it-проектов", "менеджер it проектов",
        "технический менеджер проектов",
    )
    if any(marker in text for marker in project_markers):
        return 0

    adjacent_markers = (
        "delivery manager", "delivery lead", "delivery",
        "implementation manager", "руководитель внедрения", "руководитель реализации",
    )
    if any(marker in text for marker in adjacent_markers):
        return 1

    deprioritized_markers = (
        "program manager", "programme manager", "portfolio", "pmo",
        "project office", "руководитель программы", "проектного офиса", "портфел",
        "cto", "cio", "chief technology officer", "chief information officer",
        "it director", "it-директор", "ит-директор", "технический директор",
        "директор по ит", "директор по информационным технологиям",
        "head of engineering", "engineering manager", "руководитель разработки",
        "head of product", "product lead", "product owner", "product manager",
        "руководитель продукта", "менеджер продукта", "it lead", "ит лидер",
    )
    if any(marker in text for marker in deprioritized_markers):
        return 3

    return 2

def expand_project_delivery_search_queries(queries: list[str]) -> list[str]:
    """Add delivery-title aliases when the active search is Project Management."""
    cleaned = [
        base.clean_text(str(query))
        for query in queries
        if base.clean_text(str(query))
    ]

    has_project_positioning = any(
        _search_query_priority(query) == 0
        for query in cleaned
    )
    if not has_project_positioning:
        return prioritize_search_queries(cleaned)

    expanded = [*cleaned, *DELIVERY_SEARCH_ALIASES]
    return prioritize_search_queries(expanded)


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
