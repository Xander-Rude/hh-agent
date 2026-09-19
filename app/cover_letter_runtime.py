from __future__ import annotations

import json
import re
from collections.abc import Iterable


AI_PROJECT_URL = "https://rudenko.one/hh-agent.html"

_BAD_PHRASES = (
    "многолетний опыт",
    "на уровне senior/lead",
    "идеально подходит",
    "идеально соответств",
    "полностью соответств",
    "many years of experience",
    "senior/lead level",
    "perfect fit",
    "perfectly matches",
    "fully matches",
)

_SCALE_FACT_PATTERNS = (
    r"\b30\+\s*(?:it[- ]?)?(?:проект|project)",
    r"\bпортфел\w*\s+30\+",
    r"(?:команд\w*|подразделен\w*)\s+(?:до\s+)?70\b",
    r"\b70[- ](?:person|people|member)",
    r"\bнайм\w*\s+(?:более\s+)?40\+?",
    r"\b40\+\s*(?:hires|hired|specialists)",
    r"\bc-level\b",
    r"\bceo-1\b",
    r"\b350\s*(?:млн|million)\b",
    r"\b350m\b",
    r"\bpmo\b",
    r"\bhead of pmo\b",
)

_SCOPE_MARKERS_RU = (
    "roadmap",
    "требован",
    "срок",
    "риск",
    "изменен",
    "ресурс",
    "бюджет",
    "стейкхолдер",
    "эксплуатац",
)

_SCOPE_MARKERS_EN = (
    "roadmap",
    "requirements",
    "timeline",
    "risk",
    "change",
    "resource",
    "budget",
    "stakeholder",
    "operations",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def parse_strengths(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return [raw]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [raw]

    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]

    return []


def _scale_fact_count(text: str) -> int:
    normalized = _normalize(text)
    return sum(
        1
        for pattern in _SCALE_FACT_PATTERNS
        if re.search(pattern, normalized, flags=re.IGNORECASE)
    )


def _scope_marker_count(text: str) -> int:
    normalized = _normalize(text)
    markers = _SCOPE_MARKERS_RU + _SCOPE_MARKERS_EN
    return sum(1 for marker in markers if marker in normalized)


def is_oversold_cover_letter(text: str) -> bool:
    normalized = _normalize(text)
    if any(phrase in normalized for phrase in _BAD_PHRASES):
        return True
    if _scale_fact_count(normalized) > 1:
        return True
    if _scope_marker_count(normalized) >= 6:
        return True
    return False


def _direct_strengths(strengths: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for item in parse_strengths(strengths):
        normalized = _normalize(item)
        if not normalized or normalized in seen:
            continue
        if _scale_fact_count(item):
            continue
        if any(phrase in normalized for phrase in _BAD_PHRASES):
            continue
        seen.add(normalized)
        result.append(item.rstrip(" .;"))

    return result[:2]


def calibrate_stored_cover_letter(
    text: str | None,
    strengths: object = None,
) -> str:
    """
    Runtime guard for persisted cover letters.

    New evaluations are calibrated in app.evaluator, but old database rows may
    still contain promotional drafts. This guard is intentionally deterministic
    so Telegram and the apply worker never reuse an obviously oversold letter.
    """
    current = (text or "").strip()
    if current and not is_oversold_cover_letter(current):
        return current

    english = bool(current) and len(re.findall(r"[A-Za-z]", current)) > len(
        re.findall(r"[А-Яа-яЁё]", current)
    )
    direct = _direct_strengths(strengths)
    keep_ai_project = AI_PROJECT_URL in current

    if english:
        parts = [
            "Hello!",
            "",
            "My core profile is end-to-end IT project management, from requirements "
            "and planning through delivery and production launch.",
        ]
        if direct:
            parts.append(
                "The most relevant overlap for this role is: "
                + "; ".join(direct)
                + "."
            )
        if keep_ai_project:
            parts.append(
                "I also develop my own AI-agent project that automates the vacancy "
                f"workflow: {AI_PROJECT_URL}"
            )
        parts.extend(["", "Best regards,", "Aleksandr Rudenko"])
        return "\n".join(parts).strip()

    parts = [
        "Здравствуйте!",
        "",
        "Мой основной профиль - управление IT-проектами полного цикла: "
        "от требований и планирования до delivery и запуска в production.",
    ]
    if direct:
        parts.append(
            "Для этой позиции наиболее релевантны: "
            + "; ".join(direct)
            + "."
        )
    if keep_ai_project:
        parts.append(
            "Также развиваю собственный AI-agent проект, который автоматизирует "
            f"workflow работы с вакансиями: {AI_PROJECT_URL}"
        )
    parts.extend(["", "С уважением,", "Александр Руденко"])
    return "\n".join(parts).strip()
