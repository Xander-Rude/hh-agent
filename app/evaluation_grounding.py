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


def _norm(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    return " ".join(text.split())


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
    that have no lexical evidence in the vacancy. The final decision is derived
    from the weighted dimension score plus grounded must-have/red-flag blockers.
    """
    result.must_have_missing = _ground_items(
        "must_have_missing", result.must_have_missing, vacancy
    )
    result.nice_to_have_missing = _ground_items(
        "nice_to_have_missing", result.nice_to_have_missing, vacancy
    )
    result.gaps = _ground_items("gaps", result.gaps, vacancy)
    result.red_flags = _ground_items("red_flags", result.red_flags, vacancy)

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
