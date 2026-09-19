from __future__ import annotations

import os
import re

from app.models import VacancyEvaluation


ROLE_MARKERS = (
    "project manager",
    "program manager",
    "programme manager",
    "product manager",
    "product owner",
    "head of product",
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
    "менеджер продукта",
    "руководитель продукта",
)

# Senior leadership titles that are relevant only when the vacancy itself is
# clearly in the IT / digital / AI / engineering management space.  This keeps
# generic titles such as "Head of Sales" from being promoted by the policy.
LEADERSHIP_ROLE_MARKERS = (
    "cto",
    "cio",
    "chief technology officer",
    "chief information officer",
    "it director",
    "technology director",
    "director of engineering",
    "head of engineering",
    "head of technology",
    "head of it",
    "руководитель направления",
    "руководитель департамента",
    "руководитель управления",
    "директор по информационным технологиям",
    "директор по ит",
    "директор по it",
    "технический директор",
    "директор по технологиям",
    "глава направления",
)

TECH_MANAGEMENT_CONTEXT_MARKERS = (
    "ai/ml",
    "artificial intelligence",
    "machine learning",
    "data science",
    "data platform",
    "data & ai",
    "информационн",
    "цифров",
    "технолог",
    "искусственн интеллект",
    "машинн обуч",
    "ии-проект",
    "ии проект",
    "ml-проект",
    "ml проект",
    "разработ",
    "engineering",
    "software",
    "платформ",
    "infrastructure",
    "инфраструктур",
    "архитектур",
)

# Deterministic evidence that the vacancy itself, rather than merely the
# employer or title, belongs to an IT / digital / technology delivery scope.
# These markers are intentionally more specific than generic words such as
# "project", "product", "technology" or "digital".
IT_SCOPE_MARKERS = (
    "it-проект",
    "it проект",
    "it project",
    "ит-проект",
    "ит проект",
    "информационн технолог",
    "информационн систем",
    "программное обеспечение",
    "software",
    "software development",
    "разработк",
    "backend",
    "frontend",
    "qa ",
    "quality assurance",
    "devops",
    "sre ",
    "api",
    "интеграц",
    "microservice",
    "микросервис",
    "platform",
    "платформ",
    "infrastructure",
    "инфраструктур",
    "cloud",
    "облач",
    "architecture",
    "архитектур",
    "data platform",
    "data engineering",
    "data science",
    "machine learning",
    "ai/ml",
    "artificial intelligence",
    "genai",
    "llm",
    "rag",
    "ai-проект",
    "ai project",
    "ai-продукт",
    "ai product",
    "ии-проект",
    "ии проект",
    "искусственн интеллект",
    "машинн обуч",
    "кибербезопас",
    "cybersecurity",
    "информационн безопас",
    "crm",
    "erp",
    "sap",
    "1с",
    "web ",
    "web-",
    "mobile",
    "мобильн прилож",
    "личный кабинет",
    "цифровой продукт",
    "цифровая платформа",
    "цифровая трансформац",
    "цифровизац",
    "автоматизац",
    "hardware",
    "embedded",
    "firmware",
    "iot",
    "телеком",
    "сервер",
    "server",
)

# Strong evidence that a generic PM/Product title is actually about a
# non-IT function. Explicit IT_SCOPE_MARKERS always win, so an IT project in
# construction/oil&gas is still allowed when the vacancy really describes IT.
STRONG_NON_IT_SCOPE_MARKERS = (
    "строитель",
    "строительно",
    "смр",
    "инженер пто",
    "девелопмент недвижимости",
    "недвижимост",
    "мебел",
    "коммуникационн дизайн",
    "рекламн коммуникац",
    "маркетинг",
    "продаж",
    "hr ",
    "hr-",
    "персонал",
    "c&b",
    "compensation",
    "вознагражден",
    "юридическ",
    "закуп",
    "снабжен",
    "нефтегаз",
    "бурен",
    "горнодобы",
    "операционн эффективност",
    "финансовая функция",
    "бухгалтер",
    "hvac",
    "холодильн оборудован",
)

RESUME_PM_MARKERS = (
    "управление it-проектами",
    "управление проектами",
    "руководитель проекта",
    "руководитель проектов",
    "менеджер проектов",
    "project manager",
    "project management",
    "delivery manager",
    "delivery management",
    "program manager",
    "program management",
    "управление программ",
    "управление портфелем",
    "портфель 30+",
    "30+ проектов",
    "pmo",
    "roadmap",
    "управление рисками",
    "управление изменениями",
    "до 70 человек",
    "найм 40+",
    "350 млн",
    "c-level",
    "ceo-1",
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
    "опыт работы менеджером проектов",
    "опыт менеджером проектов",
    "опыт управления проектами",
    "project manager experience",
    "program manager experience",
)

# Office work is explicitly acceptable for this candidate.  The evaluator may
# still describe 5/2 onsite work as "less preferable"; that is not a mismatch
# and must never become a red flag / gap / missing requirement.
OFFICE_NEGATIVE_MARKERS = (
    "офис 5/2",
    "офисе 5/2",
    "работа в офисе",
    "работы в офисе",
    "только офис",
    "полностью офис",
    "без возможности удален",
    "без удаленки",
    "office 5/2",
    "office-only",
    "office only",
    "on-site",
    "onsite",
    "no remote",
    "without remote",
)

# These are frequently over-interpreted by the LLM as proof that a management
# vacancy is secretly an IC/engineering role.  For leadership vacancies they
# are not critical mismatches unless the vacancy explicitly requires personal
# hands-on implementation.
MANAGEMENT_TECH_STACK_NEGATIVE_MARKERS = (
    "python",
    "git",
    "data science",
    "технический стек",
    "техническому стеку",
    "технического стека",
    "чистый менеджмент",
    "навыков разработки",
    "навыки разработки",
    "основ обучения моделей",
    "обучения моделей",
    "технический лидер",
    "техническому лидеру",
    "technical lead",
    "программирован",
    "писать код",
    "coding",
)

HANDS_ON_TECH_REQUIREMENT_MARKERS = (
    "лично писать код",
    "писать production code",
    "писать код на python",
    "разрабатывать на python",
    "программировать на python",
    "разрабатывать ml-модели",
    "разрабатывать ml модели",
    "разрабатывать модели машинного обучения",
    "обучать ml-модели",
    "обучать ml модели",
    "обучать модели машинного обучения",
    "тренировать модели",
    "hands-on",
    "hands on",
    "write production code",
    "write code",
    "implement ml models",
    "train ml models",
    "machine learning engineer",
    "ml engineer",
    "data scientist",
    "python developer",
    "python-разработчик",
)

NON_CANDIDATE_REQUIREMENT_MARKERS = (
    "зарплата",
    "зарплат",
    "salary",
    "компенсац",
    "вилка",
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
            print(f"[MANAGEMENT POLICY] removed false negative: {value}")
            continue
        result.append(value)
    return result


def _has_deterministic_it_scope(vacancy: str) -> bool:
    return _contains_any(vacancy, IT_SCOPE_MARKERS)


def _has_strong_non_it_scope(vacancy: str) -> bool:
    return _contains_any(vacancy, STRONG_NON_IT_SCOPE_MARKERS)


def _is_it_scope_relevant(
    result: VacancyEvaluation,
    vacancy: str,
) -> bool:
    # Explicit technical evidence in the vacancy is the strongest signal and
    # can rescue an occasional LLM false negative.
    if _has_deterministic_it_scope(vacancy):
        return True

    # Strong non-IT context blocks semantic optimism from generic PM/Product
    # wording. This is the class that leaked communication design, furniture,
    # construction and oil&gas operations into the Telegram feed.
    if _has_strong_non_it_scope(vacancy):
        return False

    # For genuinely ambiguous language, let the evaluator make the semantic
    # call. None is intentionally not accepted: new evaluations are instructed
    # to return the field explicitly, while old/partial results fail closed.
    return result.it_relevant is True


def _is_management_role_relevant(vacancy: str) -> bool:
    if _contains_any(vacancy, ROLE_MARKERS):
        return True

    return (
        _contains_any(vacancy, LEADERSHIP_ROLE_MARKERS)
        and _contains_any(vacancy, TECH_MANAGEMENT_CONTEXT_MARKERS)
    )


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

    if any(marker in vacancy_norm for marker in ("product", "продукт", "roadmap", "метрик", "customer", "клиент")):
        strengths.append(
            "работал с roadmap, требованиями, приоритизацией и развитием IT-продуктов"
        )

    if not strengths:
        strengths.extend(
            [
                "управлял IT-проектами полного цикла и портфелем 30+ проектов",
                "руководил крупными IT-командами и работал с C-level и бизнес-заказчиками",
            ]
        )

    deduped: list[str] = []
    for item in strengths:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]


def _vacancy_focus(vacancy: str, language: str) -> str:
    text = _norm(vacancy)

    if language == "en":
        focuses: list[str] = []
        if any(marker in text for marker in ("automation", "automat", "api", "integration")):
            focuses.append("automation and integrations")
        if any(marker in text for marker in ("platform", "infrastructure", "service", "support")):
            focuses.append("platform/service development and operations")
        if any(marker in text for marker in ("product", "roadmap", "metric", "customer")):
            focuses.append("product roadmap, prioritisation and outcomes")
        if any(marker in text for marker in ("stakeholder", "business", "cross-functional", "cross functional")):
            focuses.append("cross-functional stakeholder coordination")
        if any(marker in text for marker in ("team", "resource", "budget", "delivery")):
            focuses.append("delivery, team and resource management")
        return ", ".join(focuses[:2])

    focuses_ru: list[str] = []
    if any(marker in text for marker in ("автоматизац", "api", "интеграц")):
        focuses_ru.append("автоматизация и интеграции")
    if any(marker in text for marker in ("платформ", "инфраструктур", "поддержк", "сервис")):
        focuses_ru.append("развитие платформенных/сервисных решений и эксплуатация")
    if any(marker in text for marker in ("продукт", "roadmap", "метрик", "клиент", "приорит")):
        focuses_ru.append("roadmap, приоритизация и развитие продукта")
    if any(marker in text for marker in ("stakeholder", "стейкхолдер", "бизнес", "заказчик")):
        focuses_ru.append("координация бизнеса и технических стейкхолдеров")
    if any(marker in text for marker in ("команд", "ресурс", "бюджет", "delivery", "срок")):
        focuses_ru.append("delivery, команды, сроки и ресурсы")
    return ", ".join(focuses_ru[:2])


def _build_cover_letter(vacancy: str, language: str) -> str:
    """Conservative fallback used only when the evaluator produced no letter.

    The main evaluator owns cover-letter generation and calibration.  This
    deterministic builder exists for the rare case where downstream policy
    promotes a previously rejected vacancy to review/apply after the evaluator
    returned an empty cover letter.
    """
    focus = _vacancy_focus(vacancy, language)

    if language == "en":
        focus_sentence = (
            f"For this role, the closest overlap is {focus}. "
            if focus
            else "The role overlaps with my core project-delivery scope. "
        )
        return (
            "Hello!\n\n"
            "My core profile is end-to-end IT project and delivery management. "
            + focus_sentence
            + "I have led projects from requirements and planning through "
            "production rollout and further development.\n\n"
            "Best regards,\nAleksandr Rudenko"
        )

    focus_sentence = (
        f"Для этой позиции наиболее релевантны задачи в части: {focus}. "
        if focus
        else "Основной контур задач позиции пересекается с моим проектным опытом. "
    )
    return (
        "Здравствуйте!\n\n"
        "Мой основной профиль - управление IT-проектами и delivery полного цикла. "
        + focus_sentence
        + "Вёл проекты от требований и планирования до запуска в production "
        "и дальнейшего развития.\n\n"
        "С уважением,\nАлександр Руденко"
    )

def _language(vacancy: str) -> str:
    cyr = len(re.findall(r"[А-Яа-яЁё]", vacancy or ""))
    lat = len(re.findall(r"[A-Za-z]", vacancy or ""))
    return "ru" if cyr >= lat else "en"


def _candidate_recommendation(result: VacancyEvaluation, language: str) -> str:
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
    """Correct impossible LLM contradictions for management vacancies."""

    # Office / no-remote is no longer a negative preference.  Clean it for all
    # vacancies before deciding whether the rest of the management policy
    # applies.
    result.must_have_missing = _clean_items(
        result.must_have_missing,
        OFFICE_NEGATIVE_MARKERS,
    )
    result.nice_to_have_missing = _clean_items(
        result.nice_to_have_missing,
        OFFICE_NEGATIVE_MARKERS,
    )
    result.gaps = _clean_items(
        result.gaps,
        OFFICE_NEGATIVE_MARKERS,
    )
    result.red_flags = _clean_items(
        result.red_flags,
        OFFICE_NEGATIVE_MARKERS,
    )

    deterministic_it_scope = _has_deterministic_it_scope(vacancy)
    strong_non_it_scope = _has_strong_non_it_scope(vacancy)
    it_scope_relevant = _is_it_scope_relevant(
        result,
        vacancy,
    )

    if not it_scope_relevant:
        print(
            "[IT SCOPE POLICY] reject non-IT vacancy: "
            f"llm_it_relevant={result.it_relevant} "
            f"deterministic_it={deterministic_it_scope} "
            f"strong_non_it={strong_non_it_scope}"
        )

        reason = (
            "Фактический scope вакансии не относится к IT, цифровым продуктам "
            "или технологическому delivery."
        )
        if not any(_norm(reason) == _norm(item) for item in result.red_flags or []):
            result.red_flags = [reason, *(result.red_flags or [])]

        result.role_match = min(int(result.role_match or 0), 25)
        result.domain_match = min(int(result.domain_match or 0), 20)
        result.responsibility_match = min(
            int(result.responsibility_match or 0),
            55,
        )
        result.score = _score(
            int(result.role_match or 0),
            int(result.seniority_match or 0),
            int(result.domain_match or 0),
            int(result.responsibility_match or 0),
        )
        result.decision = "reject"
        result.cover_letter = ""
        result.recommendation = _candidate_recommendation(
            result,
            _language(vacancy),
        )
        return result

    role_relevant = _is_management_role_relevant(vacancy)
    resume_confirms_pm = _contains_any(resume, RESUME_PM_MARKERS)

    if not (role_relevant and resume_confirms_pm):
        return result

    # For a management/leadership vacancy, knowledge of Python/Git/ML basics is
    # not by itself evidence of an IC mismatch.  Keep the negative only when
    # the vacancy explicitly asks the person to implement code/models hands-on.
    if not _contains_any(vacancy, HANDS_ON_TECH_REQUIREMENT_MARKERS):
        result.must_have_missing = _clean_items(
            result.must_have_missing,
            MANAGEMENT_TECH_STACK_NEGATIVE_MARKERS,
        )
        result.nice_to_have_missing = _clean_items(
            result.nice_to_have_missing,
            MANAGEMENT_TECH_STACK_NEGATIVE_MARKERS,
        )
        result.gaps = _clean_items(
            result.gaps,
            MANAGEMENT_TECH_STACK_NEGATIVE_MARKERS,
        )
        result.red_flags = _clean_items(
            result.red_flags,
            MANAGEMENT_TECH_STACK_NEGATIVE_MARKERS,
        )

    old_role = int(result.role_match or 0)
    result.role_match = max(old_role, 90)
    if result.role_match != old_role:
        print(f"[MANAGEMENT POLICY] role_match floor: {old_role} -> {result.role_match}")

    old_domain = int(result.domain_match or 0)
    responsibility = int(result.responsibility_match or 0)
    if responsibility >= 70:
        result.domain_match = max(old_domain, 60)
        if result.domain_match != old_domain:
            print(
                f"[MANAGEMENT POLICY] domain_match floor: {old_domain} -> {result.domain_match}"
            )

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

    result.must_have_missing = _clean_items(
        result.must_have_missing,
        NON_CANDIDATE_REQUIREMENT_MARKERS,
    )
    result.gaps = _clean_items(
        result.gaps,
        NON_CANDIDATE_REQUIREMENT_MARKERS,
    )

    result.seniority_match = max(int(result.seniority_match or 0), 82)
    result.responsibility_match = max(int(result.responsibility_match or 0), 80)

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
    has_missing_must_have = bool(result.must_have_missing)

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

    language = _language(vacancy)

    if result.decision != "reject":
        summary_norm = _norm(result.summary)
        if any(_norm(p) in summary_norm for p in GENERIC_REJECT_PHRASES):
            result.summary = (
                "Профиль соответствует управленческой части роли; возможные расхождения относятся к предметному домену или отдельным специализированным требованиям."
            )

        # The evaluator has already generated, normalized and anti-oversell
        # checked the cover letter.  Do not overwrite it here with a generic
        # management template: doing so used to re-introduce stacked scale
        # facts and also erased AI-project context added upstream.
        if (
            not (result.cover_letter or "").strip()
            or LOW_EXPERIENCE_RE.search(result.cover_letter or "")
        ):
            result.cover_letter = _build_cover_letter(
                vacancy,
                language,
            )
    else:
        # A downstream policy may downgrade a previously acceptable vacancy.
        # Never leave a stale letter attached to a final reject.
        result.cover_letter = ""

    result.recommendation = _candidate_recommendation(
        result,
        language,
    )

    return result
