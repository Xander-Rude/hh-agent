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
        # The canonical profile is Russian; keep the English fallback concise
        # and factual instead of inventing a numeric experience duration.
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


def apply_management_policy(
    result: VacancyEvaluation,
    *,
    resume: str,
    vacancy: str,
) -> VacancyEvaluation:
    """Correct impossible LLM contradictions for confirmed PM experience.

    This policy does not invent domain expertise or certifications. It only
    protects baseline project-management competencies and prevents a matching
    management role from receiving near-zero role/domain scores merely because
    the vacancy uses different wording.
    """
    role_relevant = _contains_any(vacancy, ROLE_MARKERS)
    resume_confirms_pm = _contains_any(resume, RESUME_PM_MARKERS)

    if not (role_relevant and resume_confirms_pm):
        return result

    # A confirmed senior PM profile cannot be a 0-3 role match for an explicit
    # PM/Program/Product/Technical PM vacancy.
    old_role = int(result.role_match or 0)
    result.role_match = max(old_role, 78)
    if result.role_match != old_role:
        print(f"[PM POLICY] role_match floor: {old_role} -> {result.role_match}")

    # Domain is not the same thing as role competence. When responsibilities
    # already match strongly, a new industry is a transferable-context gap,
    # not a near-zero domain score.
    old_domain = int(result.domain_match or 0)
    responsibility = int(result.responsibility_match or 0)
    if responsibility >= 70:
        result.domain_match = max(old_domain, 45)
        if result.domain_match != old_domain:
            print(f"[PM POLICY] domain_match floor: {old_domain} -> {result.domain_match}")

    # User-confirmed professional baseline of an experienced project manager.
    # PMBOK/PRINCE2 here means practical command of the frameworks/practices,
    # never a claim of certification.
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

    if result.decision != "reject":
        recommendation_norm = _norm(result.recommendation)
        summary_norm = _norm(result.summary)
        if any(_norm(p) in recommendation_norm for p in GENERIC_REJECT_PHRASES):
            result.recommendation = (
                "Роль и основной управленческий контур релевантны; предметный домен и точечные технические требования следует проверить на интервью."
            )
        if any(_norm(p) in summary_norm for p in GENERIC_REJECT_PHRASES):
            result.summary = (
                "Профиль соответствует управленческой части роли; возможные расхождения относятся к предметному домену или отдельным специализированным требованиям."
            )

        cover = (result.cover_letter or "").strip()
        # Do not send artificially weak claims such as "2+ years" when the CV
        # clearly describes a senior/lead-scale career. Replace with factual
        # scale rather than inventing a duration.
        if not cover or LOW_EXPERIENCE_RE.search(cover):
            result.cover_letter = _build_cover_letter(
                vacancy,
                _language(vacancy),
            )

    return result
