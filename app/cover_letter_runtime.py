from __future__ import annotations

import json
import os
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


def _binding_text(value: str | None) -> str:
    return re.sub(
        r"[^0-9a-zа-я]+",
        " ",
        _normalize(value or ""),
        flags=re.IGNORECASE,
    ).strip()


def is_vacancy_bound_cover_letter(
    text: str | None,
    *,
    vacancy_title: str,
    vacancy_company: str | None,
) -> bool:
    """Return True only when the letter explicitly identifies this vacancy."""
    body = _binding_text(text)
    title = _binding_text(vacancy_title)
    company = _binding_text(vacancy_company)

    if not body or not title:
        return False
    if title not in body:
        return False
    if company and company not in body:
        return False
    return True


def build_legacy_vacancy_cover_letter(
    *,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    stored_text: str | None,
    strengths: object = None,
) -> str:
    """Calibrate a legacy letter, then bind it explicitly to the vacancy.

    Legacy evaluations can contain a useful tailored draft, but the runtime
    calibration fallback used to collapse many vacancies into the same generic
    text.  This wrapper preserves the calibrated body while making the final
    artifact unambiguously vacancy-specific.
    """
    safe = calibrate_stored_cover_letter(stored_text, strengths).strip()

    if is_vacancy_bound_cover_letter(
        safe,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
    ):
        return safe

    language_sample = " ".join(
        [vacancy_title or "", vacancy_company or "", vacancy_description or "", safe]
    )
    cyr = len(re.findall(r"[А-Яа-яЁё]", language_sample))
    lat = len(re.findall(r"[A-Za-z]", language_sample))
    english = lat > cyr

    title = re.sub(r"\s+", " ", vacancy_title or "").strip() or "position"
    company = re.sub(r"\s+", " ", vacancy_company or "").strip()

    if english:
        opening = (
            f'I am interested in the "{title}" role'
            + (f" at {company}." if company else ".")
        )
        greetings = {"hello!", "hello", "dear hiring team,", "dear hiring team"}
    else:
        opening = (
            f'Рассматриваю позицию «{title}»'
            + (f" в {company}." if company else ".")
        )
        greetings = {"здравствуйте!", "здравствуйте"}

    lines = safe.splitlines()
    if lines and lines[0].strip().lower() in greetings:
        tail = lines[1:]
        while tail and not tail[0].strip():
            tail.pop(0)
        result = "\n".join(
            [lines[0].strip(), "", opening, *tail]
        ).strip()
    elif safe:
        result = (opening + "\n\n" + safe).strip()
    else:
        result = opening

    if not is_vacancy_bound_cover_letter(
        result,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
    ):
        raise ValueError("cover letter is not bound to the target vacancy")
    return result


_CLEAN_AI_RE = re.compile(
    r"(?:\bAI\b|\bML\b|\bLLM\b|\bGenAI\b|\bRAG\b|"
    r"искусственн\w*\s+интеллект|машинн\w*\s+обучен|"
    r"ИИ[- ]?агент|AI[- ]?agent)",
    re.IGNORECASE,
)


def _clean_requirement_text(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .;:-")
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return shortened + "…"


def _clean_extraction_payload(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_cover_requirements(extraction: dict) -> list[dict]:
    requirements = [
        item
        for item in (extraction.get("requirements") or [])
        if isinstance(item, dict)
    ]
    ranked = sorted(
        requirements,
        key=lambda item: (
            item.get("criticality") not in {"non_negotiable", "core"},
            item.get("criticality") == "preferred",
        ),
    )

    result: list[dict] = []
    seen: set[str] = set()
    for item in ranked:
        if item.get("match_quality") != "full":
            continue
        if item.get("category") == "education_clearance":
            continue
        source_text = _clean_requirement_text(item.get("source_text"))
        normalized = _normalize(source_text)
        if not source_text or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
        if len(result) >= 2:
            break
    return result


def _clean_cover_is_ai_relevant(
    *,
    vacancy_title: str,
    vacancy_description: str,
    extraction: dict,
) -> bool:
    text = " ".join(
        [
            vacancy_title or "",
            vacancy_description or "",
            " ".join(
                str(item.get("source_text") or "")
                for item in (extraction.get("requirements") or [])
                if isinstance(item, dict)
            ),
        ]
    )
    return bool(_CLEAN_AI_RE.search(text))


def _clean_cover_ai_visible_fact(extraction: dict) -> str | None:
    for item in extraction.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_text") or "")
        evidence = str(item.get("candidate_evidence") or "")
        if not _CLEAN_AI_RE.search(source + " " + evidence):
            continue
        if "autofaq" in evidence.lower():
            return "В AI-контексте внедрял AutoFAQ для Q&A по документации."
    return None


def _ai_project_cover_enabled() -> bool:
    return os.getenv(
        "HH_ENABLE_AI_PROJECT_COVER_LETTER",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}


def build_clean_cover_letter(
    *,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    extraction_json: object,
) -> str:
    """Build the final CLEAN cover letter from current CLEAN evidence.

    Unlike the legacy fallback, this text is tied to the current vacancy and
    current CLEAN extraction.  Only full matches are surfaced as overlap; a
    partial/missing requirement is never promoted into a claimed strength.
    """

    extraction = _clean_extraction_payload(extraction_json)
    matched = _clean_cover_requirements(extraction)
    matched_text = [
        _clean_requirement_text(item.get("source_text"))
        for item in matched
    ]

    language_sample = " ".join(
        [vacancy_title or "", vacancy_description or ""]
    )
    cyr = len(re.findall(r"[А-Яа-яЁё]", language_sample))
    lat = len(re.findall(r"[A-Za-z]", language_sample))
    english = lat > cyr

    ai_relevant = _clean_cover_is_ai_relevant(
        vacancy_title=vacancy_title,
        vacancy_description=vacancy_description,
        extraction=extraction,
    )
    ai_visible_fact = _clean_cover_ai_visible_fact(extraction)

    title = _clean_requirement_text(vacancy_title, 140) or "позицию"
    company = _clean_requirement_text(vacancy_company or "", 120)

    if english:
        opening = (
            f'I am interested in the "{title}" role'
            + (f" at {company}." if company else ".")
        )
        parts = ["Hello!", "", opening]
        if matched_text:
            parts.append(
                "The closest overlap with my experience is: "
                + "; ".join(matched_text)
                + "."
            )
        parts.append(
            "I have led IT projects end to end, from requirements and planning "
            "through delivery, production launch and further development."
        )
        if ai_relevant and ai_visible_fact:
            parts.append(
                "My recruiter-visible AI experience includes AutoFAQ for "
                "documentation Q&A."
            )
        if ai_relevant and _ai_project_cover_enabled():
            parts.append(
                "I also develop my own AI-agent project for automating the "
                f"vacancy workflow: {AI_PROJECT_URL}"
            )
        parts.extend(["", "Best regards,", "Aleksandr Rudenko"])
        return "\n".join(parts).strip()

    opening = (
        f'Рассматриваю позицию «{title}»'
        + (f" в {company}." if company else ".")
    )
    parts = ["Здравствуйте!", "", opening]
    if matched_text:
        parts.append(
            "По опыту наиболее близки задачи: "
            + "; ".join(matched_text)
            + "."
        )
    parts.append(
        "Вёл IT-проекты полного цикла: от требований и планирования "
        "до delivery, запуска в production и дальнейшего развития."
    )
    if ai_relevant and ai_visible_fact:
        parts.append(ai_visible_fact)
    if ai_relevant and _ai_project_cover_enabled():
        parts.append(
            "Также развиваю собственный AI-agent проект, который автоматизирует "
            f"workflow работы с вакансиями: {AI_PROJECT_URL}"
        )
    parts.extend(["", "С уважением,", "Александр Руденко"])
    return "\n".join(parts).strip()
