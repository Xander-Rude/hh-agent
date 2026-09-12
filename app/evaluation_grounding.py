from __future__ import annotations

import os
import re

from app.models import VacancyEvaluation


_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+#./&-]*")

_STOP_WORDS = {
    "опыт",
    "работы",
    "работа",
    "работать",
    "работал",
    "наличие",
    "отсутствие",
    "отсутствует",
    "нет",
    "нужен",
    "нужна",
    "нужно",
    "требуется",
    "требования",
    "требование",
    "знание",
    "знания",
    "глубокое",
    "уверенное",
    "владение",
    "понимание",
    "кандидат",
    "кандидата",
    "профиль",
    "профиле",
    "роль",
    "роли",
    "уровень",
    "сильный",
    "сильным",
    "прямой",
    "прямого",
    "именно",
    "сфера",
    "сфере",
    "область",
    "области",
    "навык",
    "навыки",
    "навыков",
    "подтвержден",
    "подтверждено",
    "указан",
    "указано",
    "позиционируется",
    "experience",
    "experienced",
    "required",
    "requirement",
    "requirements",
    "knowledge",
    "understanding",
    "candidate",
    "profile",
    "role",
    "strong",
    "deep",
    "direct",
    "skills",
    "skill",
    "working",
    "work",
    "with",
    "from",
    "this",
    "that",
    "into",
    "for",
    "the",
    "and",
    "или",
    "для",
    "как",
    "при",
    "что",
    "это",
    "его",
    "ее",
    "также",
    "более",
}

# The user now accepts office, hybrid and remote work. A work format is therefore
# informational, not a blocker. Location/relocation can still be a separate gap.
WORK_FORMAT_MARKERS = (
    "работа в офисе",
    "работу в офисе",
    "офисный формат",
    "офисе",
    "office-based",
    "office based",
    "on-site",
    "onsite",
    "hybrid",
    "гибрид",
    "remote",
    "удален",
    "work format",
    "формат работы",
)

# Absence of evidence in a CV is not by itself a decision-blocking red flag.
# Such items remain visible as gaps/must-haves and can still lower the score.
EVIDENCE_UNCERTAINTY_MARKERS = (
    "не подтвержден в резюме",
    "не подтверждено в резюме",
    "не подтверждена в резюме",
    "не подтверждены в резюме",
    "не подтверждается резюме",
    "нет в резюме",
    "не указано в резюме",
    "не указан в резюме",
    "не указана в резюме",
    "не видно в резюме",
    "not confirmed in the resume",
    "not confirmed by the resume",
    "not evidenced in the resume",
    "not shown in the resume",
    "not listed in the resume",
    "not in the resume",
    "not in cv",
)

# The role filter intentionally allows executive technology leadership. Do not
# let the evaluator re-create the old "PM/Program/Delivery only" box as a blocker.
STALE_PROFILE_BOX_MARKERS = (
    "senior project",
    "project/program",
    "program/delivery",
    "project/program/delivery",
    "project manager",
    "program manager",
    "programme manager",
    "delivery manager",
    "delivery management",
    "профиль кандидата",
    "сильным управленцем",
    "сильный управленец",
    "управленческий профиль",
)

LOCATION_BLOCKER_MARKERS = (
    "релокац",
    "переезд",
    "relocation",
    "work authorization",
    "разрешение на работу",
)

TECH_LEADERSHIP_TITLE_PATTERNS = (
    re.compile(r"\b(?:cto|cpto|cio|cdto)\b", re.IGNORECASE),
    re.compile(
        r"\bchief\s+(?:technology|information|digital|product\s+(?:and|&)\s+technology)\s+officer\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:vp|vice president|head|director|managing director)\b.{0,40}\b(?:technology|engineering|it|platform|infrastructure)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:ит|it)[-\s]?директор\b", re.IGNORECASE),
    re.compile(
        r"\bдиректор\s+по\s+(?:ит|it|информационным технологиям|цифровой трансформации)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bтехническ(?:ий|ого)\s+директор", re.IGNORECASE),
    re.compile(
        r"\b(?:руководитель|директор)\s+(?:it|ит)[-\s]?(?:департамента|направления|блока)\b",
        re.IGNORECASE,
    ),
)


def _norm(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    return " ".join(text.split())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _norm(text)
    return any(_norm(marker) in normalized for marker in markers)


def _raw_tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value or "")


def _significant_tokens(value: str) -> list[str]:
    result: list[str] = []
    for raw in _raw_tokens(value):
        normalized = _norm(raw).strip("-./&")
        if not normalized or normalized in _STOP_WORDS:
            continue

        is_acronym = raw.isupper() and any(ch.isalpha() for ch in raw)
        has_special = any(ch in raw for ch in "+#./&")

        if len(normalized) >= 4 or is_acronym or has_special:
            result.append(normalized)

    return result


def _token_matches_vacancy(token: str, vacancy_tokens: set[str], vacancy_norm: str) -> bool:
    if token in vacancy_norm:
        return True

    if len(token) < 5:
        return token in vacancy_tokens

    # Russian inflections and English plural/suffix variants are common in LLM
    # paraphrases. Prefix matching is deliberately limited to meaningful tokens.
    prefix_len = 5 if len(token) >= 7 else 4
    prefix = token[:prefix_len]
    return any(
        len(candidate) >= prefix_len and candidate[:prefix_len] == prefix
        for candidate in vacancy_tokens
    )


def _is_grounded(item: str, vacancy: str) -> bool:
    vacancy_norm = _norm(vacancy)
    vacancy_tokens = {
        _norm(token).strip("-./&")
        for token in _raw_tokens(vacancy)
        if token.strip("-./&")
    }

    anchors = _significant_tokens(item)
    if not anchors:
        # No useful lexical anchor means we cannot safely prove the item was
        # hallucinated, so keep it instead of deleting a potentially valid risk.
        return True

    return any(
        _token_matches_vacancy(anchor, vacancy_tokens, vacancy_norm)
        for anchor in anchors
    )


def _ground_items(field: str, items: list[str] | None, vacancy: str) -> list[str]:
    grounded: list[str] = []
    for item in items or []:
        value = str(item).strip()
        if not value:
            continue
        if _is_grounded(value, vacancy):
            grounded.append(value)
            continue
        print(f"[GROUNDING] removed unsupported {field}: {value}")
    return grounded


def _vacancy_title(vacancy: str) -> str:
    match = re.search(r"(?im)^Название:\s*\n?\s*([^\n]+)", vacancy or "")
    return match.group(1).strip() if match else ""


def _is_target_tech_leadership(vacancy: str) -> bool:
    title = _vacancy_title(vacancy)
    return any(pattern.search(title) for pattern in TECH_LEADERSHIP_TITLE_PATTERNS)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        key = _norm(value)
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _apply_red_flag_policy(
    result: VacancyEvaluation,
    vacancy: str,
) -> VacancyEvaluation:
    """Keep red flags for real blockers; demote uncertainty and stale preferences.

    A red flag is decision-blocking, so absence of CV evidence, an accepted work
    format, or a title-only CTO/CPTO mismatch must not be allowed to become one.
    Real explicit blockers remain untouched and still force reject.
    """
    target_tech_leadership = _is_target_tech_leadership(vacancy)
    gaps = list(result.gaps or [])
    blocking: list[str] = []

    for raw in result.red_flags or []:
        value = str(raw).strip()
        if not value:
            continue

        if _contains_any(value, WORK_FORMAT_MARKERS):
            if _contains_any(value, LOCATION_BLOCKER_MARKERS):
                gaps.append(value)
                print(
                    "[RED FLAG POLICY] demoted location/work-format issue: "
                    f"{value}"
                )
            else:
                print(
                    "[RED FLAG POLICY] removed accepted work-format issue: "
                    f"{value}"
                )
            continue

        if _contains_any(value, EVIDENCE_UNCERTAINTY_MARKERS):
            gaps.append(value)
            print(
                "[RED FLAG POLICY] demoted unconfirmed CV evidence: "
                f"{value}"
            )
            continue

        if (
            target_tech_leadership
            and _contains_any(value, STALE_PROFILE_BOX_MARKERS)
        ):
            gaps.append(value)
            print(
                "[RED FLAG POLICY] demoted stale target-role mismatch: "
                f"{value}"
            )
            continue

        blocking.append(value)

    result.red_flags = _dedupe(blocking)
    result.gaps = _dedupe(gaps)

    # These titles are explicit search targets now. A hands-on architecture gap
    # belongs in responsibility_match/gaps; it must not turn the title itself into
    # a low role_match merely because the historical profile was PM-heavy.
    if target_tech_leadership:
        old_role = int(result.role_match or 0)
        result.role_match = max(old_role, 80)
        if result.role_match != old_role:
            print(
                f"[TARGET ROLE POLICY] role_match floor: "
                f"{old_role} -> {result.role_match}"
            )

    return result


def _score(result: VacancyEvaluation) -> int:
    value = (
        int(result.role_match or 0) * 0.35
        + int(result.seniority_match or 0) * 0.20
        + int(result.domain_match or 0) * 0.15
        + int(result.responsibility_match or 0) * 0.30
    )
    return max(0, min(100, int(round(value))))


def _language(vacancy: str) -> str:
    cyr = len(re.findall(r"[А-Яа-яЁё]", vacancy or ""))
    lat = len(re.findall(r"[A-Za-z]", vacancy or ""))
    return "ru" if cyr >= lat else "en"


def _recommendation(result: VacancyEvaluation, vacancy: str) -> str:
    issues: list[str] = []
    for collection in (result.red_flags, result.must_have_missing, result.gaps):
        for item in collection or []:
            value = str(item).strip()
            if value and value not in issues:
                issues.append(value)
            if len(issues) >= 2:
                break
        if len(issues) >= 2:
            break

    if _language(vacancy) == "en":
        base = {
            "apply": "Worth applying: the final grounded score is above the apply threshold.",
            "review": "Worth reviewing: the score is sufficient, but grounded requirements still need attention.",
            "reject": "Not worth applying: the grounded evaluation is below the safe apply criteria.",
        }[result.decision]
        return base + ((" Main risks: " + "; ".join(issues) + ".") if issues else "")

    base = {
        "apply": "Стоит откликаться: итоговая подтверждённая оценка выше порога отклика.",
        "review": "Стоит проверить вручную: оценка достаточная, но остались подтверждённые риски по требованиям.",
        "reject": "Не стоит откликаться: итоговая подтверждённая оценка не проходит безопасные критерии отклика.",
    }[result.decision]
    return base + ((" Основные риски: " + "; ".join(issues) + ".") if issues else "")


def ground_and_decide(
    result: VacancyEvaluation,
    *,
    vacancy: str,
) -> VacancyEvaluation:
    """Ground negative claims in vacancy text and make decision deterministic.

    LLM remains responsible for semantic scoring, but it cannot invent blockers
    that have no lexical evidence in the vacancy. Non-blocking red flags are
    demoted before the final decision so only real blockers can force reject.
    """
    result.must_have_missing = _ground_items(
        "must_have_missing", result.must_have_missing, vacancy
    )
    result.nice_to_have_missing = _ground_items(
        "nice_to_have_missing", result.nice_to_have_missing, vacancy
    )
    result.gaps = _ground_items("gaps", result.gaps, vacancy)
    result.red_flags = _ground_items("red_flags", result.red_flags, vacancy)
    result = _apply_red_flag_policy(result, vacancy)

    old_score = int(result.score or 0)
    result.score = _score(result)
    if result.score != old_score:
        print(f"[DECISION POLICY] score: {old_score} -> {result.score}")

    apply_threshold = int(os.getenv("SCORE_THRESHOLD", "80"))
    review_threshold = min(
        int(os.getenv("HH_REVIEW_THRESHOLD", "70")),
        apply_threshold,
    )

    has_red_flags = bool(result.red_flags)
    has_missing_must_have = bool(result.must_have_missing)

    old_decision = result.decision
    if (
        result.score >= apply_threshold
        and not has_red_flags
        and not has_missing_must_have
    ):
        result.decision = "apply"
    elif result.score >= review_threshold and not has_red_flags:
        result.decision = "review"
    else:
        result.decision = "reject"

    if result.decision != old_decision:
        print(
            f"[DECISION POLICY] decision: {old_decision} -> {result.decision} "
            f"(score={result.score}, must_have={has_missing_must_have}, red_flags={has_red_flags})"
        )

    result.recommendation = _recommendation(result, vacancy)
    return result
