from __future__ import annotations

import os
import re

from app.models import VacancyEvaluation


ROLE_MARKERS = (
    "project manager",
    "program manager",
    "programme manager",
    "product manager",
    "delivery manager",
    "technical project manager",
    "руководитель проекта",
    "руководитель проектов",
    "менеджер проектов",
    "менеджер it-проектов",
    "менеджер ит-проектов",
    "технический менеджер проектов",
    "технический менеджер",
    "руководитель программы",
    "руководитель программ",
)

RESUME_PM_MARKERS = (
    "управление it-проектами",
    "управление проектами",
    "project management",
    "delivery management",
    "program management",
    "управление портфелем",
    "pmo",
    "roadmap",
)

PM_BASELINE_NEGATIVE_MARKERS = (
    "agile",
    "scrum",
    "kanban",
    "less",
    "waterfall",
    "pmbok",
    "prince2",
    "project management body of knowledge",
    "управление рисками",
    "risk management",
    "управление изменениями",
    "change management",
    "управление требованиями",
    "requirements management",
    "планирование сроков",
    "управление сроками",
    "schedule management",
    "управление бюджетом",
    "budget management",
    "stakeholder",
    "стейкхолдер",
)

GENERIC_REJECT_PHRASES = (
    "кандидат не подходит",
    "не подходит для данной вакансии",
    "необходимо искать кандидата",
    "искать кандидата с более подходящим опытом",
    "отсутствия ключевых навыков и опыта",
)

LOW_EXPERIENCE_RE = re.compile(
    r"(?i)(?:опыт\s+(?:от\s+)?|более\s+)([1-3])\s*(?:лет|года|years?)"
)


def _norm(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    return " ".join(text.split())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _norm(text)
    return any(_norm(marker) in normalized for marker in markers)


def _clean_items(items: list[str] | None, markers: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for item in items or []:
        value = str(item).strip()
        if not value:
            continue
        normalized = _norm(value)
        if any(_norm(marker) in normalized for marker in markers):
            print(f"[PM POLICY] removed false negative: {value}")
            continue
        result.append(value)
    return result


def _score(
    role: int,
    seniority: int,
    domain: int,
    responsibility: int,
) -> int:
    value = (
        role * 0.35
        + seniority * 0.20
        + domain * 0.15
        + responsibility * 0.30
    )
    return max(0, min(100, int(round(value))))


def _relevant_strengths(vacancy: str) -> list[str]:
    vacancy_norm = _norm(vacancy)
    strengths: list[str] = []

    if any(marker in vacancy_norm for marker in ("project", "проект", "program", "программ")):
        strengths.append(
            "управлял IT-проектами полного цикла, портфелем 30+ проектов и параллельными инициативами"
        )

    if any(marker in vacancy_norm for marker in ("team", "команд", "resource", "ресурс")):
        strengths.append(
            "управлял крупными IT-командами до 70 человек и наймом 40+ специалистов"
        )

    if any(marker in vacancy_norm for marker in ("budget", "бюджет", "ресурс")):
        strengths.append(
            "отвечал за бюджет около 350 млн ₽ и ресурсное планирование"
        )

    if any(marker in vacancy_norm for marker in ("stakeholder", "c-level", "бизнес", "заказчик")):
        strengths.append(
            "работал с C-level, CEO-1 и бизнес-заказчиками"
        )

    if any(marker in vacancy_norm for marker in ("agile", "scrum", "kanban", "less", "waterfall", "delivery")):
        strengths.append(
            "использовал Agile, Scrum/LeSS, Kanban, Waterfall и гибридные delivery-подходы"
        )

    if not strengths:
        strengths.extend(
            [
                "управлял IT-проектами полного цикла и портфелем 30+ проектов",
                "руководил крупными IT-командами и работал с C-level и бизнес-заказчиками",
            ]
        )

    return strengths[:3]


def _build_cover_letter(vacancy: str, language: str) -> str:
    strengths = _relevant_strengths(vacancy)

    if language == "en":
        body = (
            "Hello!\n\n"
            "My background is in end-to-end IT project, program and delivery management. "
            "I have managed a portfolio of 30+ projects, large IT teams, budgets and executive stakeholders. "
            "I also work with Agile/Scrum/LeSS, Kanban, Waterfall and hybrid delivery models. "
            "The responsibilities of this role are close to the scope I have owned in previous positions."
        )
        return body + "\n\nBest regards,\nAleksandr Rudenko"

    details = "; ".join(strengths)
    return (
        "Здравствуйте!\n\n"
        "У меня многолетний опыт управления IT-проектами, программами и delivery на уровне senior/lead. "
        f"Из наиболее релевантного для этой позиции: {details}. "
        "В работе веду полный управленческий цикл: цели и roadmap, требования, сроки, риски, изменения, "
        "ресурсы, бюджет, взаимодействие со стейкхолдерами и передачу результата в эксплуатацию.\n\n"
        "С уважением,\nАлександр Руденко"
    )


def _language(vacancy: str) -> str:
    cyr = len(re.findall(r"[А-Яа-яЁё]", vacancy or ""))
    lat = len(re.findall(r"[A-Za-z]", vacancy or ""))
    return "ru" if cyr >= lat else "en"


def _candidate_recommendation(result: VacancyEvaluation, language: str) -> str:
    """Recommendation is advice to the candidate, never to a recruiter."""
    issues: list[str] = []
    for collection in (
        result.red_flags,
        result.must_have_missing,
        result.gaps,
    ):
        for item in collection or []:
            value = str(item).strip()
            if value and value not in issues:
                issues.append(value)
            if len(issues) >= 2:
                break
        if len(issues) >= 2:
            break

    if language == "en":
        if result.decision == "apply":
            base = "Worth applying: the role is a strong match for your level and core responsibilities."
        elif result.decision == "review":
            base = "Worth applying, but with some reservations: the core role is relevant, while a few requirements may be weaker matches."
        else:
            base = "Not worth applying: the mismatch is material enough that the expected return is low."

        if issues:
            return base + " Main risks: " + "; ".join(issues) + "."
        return base

    if result.decision == "apply":
        base = "Стоит откликаться: роль хорошо совпадает с твоим уровнем и основным контуром ответственности."
    elif result.decision == "review":
        base = "Стоит откликаться, но с оговорками: основной контур роли релевантен, при этом есть отдельные риски по требованиям."
    else:
        base = "Не стоит откликаться: расхождения достаточно существенные, чтобы вероятность полезного результата была низкой."

    if issues:
        return base + " Основные риски: " + "; ".join(issues) + "."
    return base


def apply_management_policy(
    result: VacancyEvaluation,
    *,
    resume: str,
    vacancy: str,
) -> VacancyEvaluation:
    """Correct impossible LLM contradictions for confirmed PM experience."""
    role_relevant = _contains_any(vacancy, ROLE_MARKERS)
    resume_confirms_pm = _contains_any(resume, RESUME_PM_MARKERS)

    if not (role_relevant and resume_confirms_pm):
        return result

    old_role = int(result.role_match or 0)
    result.role_match = max(old_role, 78)
    if result.role_match != old_role:
        print(f"[PM POLICY] role_match floor: {old_role} -> {result.role_match}")

    old_domain = int(result.domain_match or 0)
    responsibility = int(result.responsibility_match or 0)
    if responsibility >= 70:
        result.domain_match = max(old_domain, 45)
        if result.domain_match != old_domain:
            print(f"[PM POLICY] domain_match floor: {old_domain} -> {result.domain_match}")

    result.must_have_missing = _clean_items(
        result.must_have_missing,
        PM_BASELINE_NEGATIVE_MARKERS,
    )
    result.nice_to_have_missing = _clean_items(
        result.nice_to_have_missing,
        PM_BASELINE_NEGATIVE_MARKERS,
    )
    result.gaps = _clean_items(result.gaps, PM_BASELINE_NEGATIVE_MARKERS)
    result.red_flags = _clean_items(result.red_flags, PM_BASELINE_NEGATIVE_MARKERS)

    result.seniority_match = max(int(result.seniority_match or 0), 78)
    result.responsibility_match = max(int(result.responsibility_match or 0), 78)

    result.score = _score(
        int(result.role_match or 0),
        int(result.seniority_match or 0),
        int(result.domain_match or 0),
        int(result.responsibility_match or 0),
    )

    apply_threshold = int(os.getenv("SCORE_THRESHOLD", "80"))
    review_threshold = min(
        int(os.getenv("HH_REVIEW_THRESHOLD", "70")),
        apply_threshold,
    )

    has_red_flags = bool(result.red_flags)
    if result.score >= apply_threshold and not has_red_flags:
        result.decision = "apply"
    elif result.score >= review_threshold and not has_red_flags:
        result.decision = "review"
    else:
        result.decision = "reject"

    language = _language(vacancy)

    if result.decision != "reject":
        summary_norm = _norm(result.summary)
        if any(_norm(p) in summary_norm for p in GENERIC_REJECT_PHRASES):
            result.summary = (
                "Профиль соответствует управленческой части роли; возможные расхождения относятся к предметному домену или отдельным специализированным требованиям."
            )

        cover = (result.cover_letter or "").strip()
        if not cover or LOW_EXPERIENCE_RE.search(cover):
            result.cover_letter = _build_cover_letter(
                vacancy,
                language,
            )

    # Always replace model-written recruiter-facing advice with advice for the
    # candidate. This makes recommendation independent of LLM perspective.
    result.recommendation = _candidate_recommendation(
        result,
        language,
    )

    return result
