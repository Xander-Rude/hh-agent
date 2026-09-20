from __future__ import annotations

import re


MANAGEMENT_ROLE_RE = re.compile(
    r"(?:"
    r"\b(?:head|lead|manager)\b|\bteam\s*lead\b|"
    r"руководител[ья]|тимлид|менеджер"
    r")",
    re.IGNORECASE,
)

TECH_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bit\b|\bai\b|\bml\b|\bllm\b|"
    r"technical|tech|engineering|development|platform|fintech|digital|data|"
    r"техническ|технолог|разработ|платформ|финтех|цифров|данн|"
    r"искусственн\w*\s+интеллект"
    r")",
    re.IGNORECASE,
)

PRODUCT_LAUNCH_RE = re.compile(
    r"(?:"
    r"product\w*\s+launch\w*|launch\w*\s+product\w*|"
    r"продукт\w*\s+запуск\w*|запуск\w*\s+продукт\w*"
    r")",
    re.IGNORECASE,
)

OBVIOUS_NON_TARGET_RE = re.compile(
    r"(?:"
    r"\bsales\b|\bmarketing\b|\bcommercial\b|"
    r"продаж|маркетинг|реклам|коммерч|клиент"
    r")",
    re.IGNORECASE,
)


def is_management_tech_fallback(title: str) -> bool:
    """Conservative fallback for non-standard management titles.

    Career sites often name relevant roles without literal project/product/delivery
    keywords. Let those roles reach the evaluator only when the title combines a
    management marker with a clear technical context (AI/LLM/IT/platform/etc.) or
    explicitly refers to managing product launches.
    """
    value = " ".join((title or "").split())
    if not value or not MANAGEMENT_ROLE_RE.search(value):
        return False
    if OBVIOUS_NON_TARGET_RE.search(value):
        return False
    return bool(
        TECH_CONTEXT_RE.search(value)
        or PRODUCT_LAUNCH_RE.search(value)
    )
