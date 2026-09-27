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


def _binding_probe(value: str | None, *, max_words: int) -> str:
    words = _binding_text(value).split()
    return " ".join(words[:max_words])


def is_vacancy_bound_cover_letter(
    text: str | None,
    *,
    vacancy_title: str,
    vacancy_company: str | None,
) -> bool:
    """Return True only when the letter explicitly identifies this vacancy."""
    body = _binding_text(text)
    title = _binding_probe(vacancy_title, max_words=10)
    company = _binding_probe(vacancy_company, max_words=8)

    if not body or not title:
        return False
    if title not in body:
        return False
    if company and company not in body:
        return False
    return True


def _stable_variant(*values: object, count: int) -> int:
    if count <= 1:
        return 0
    seed = "|".join(_normalize(str(value or "")) for value in values)
    return sum(ord(char) for char in seed) % count


def _cover_result_fact(text: str, *, english: bool) -> str | None:
    normalized = _normalize(text)

    if _CLEAN_AI_RE.search(text):
        return None

    if (
        "security" in normalized
        or "информационн" in normalized and "безопас" in normalized
        or re.search(r"\bиб\b", normalized)
    ):
        return None

    if any(
        marker in normalized
        for marker in ("автоматиз", "automation", "цифровизац", "digitalization")
    ):
        return (
            "At Moscow City IT, I helped reduce Time-to-Market from 52 to 6 days."
            if english
            else "В ДИТ Москвы удалось сократить Time-to-Market с 52 до 6 дней."
        )

    if (
        "телеком" in normalized
        or "telecom" in normalized
        or "sla" in normalized
        or re.search(r"\b(?:bss|oss)\b", normalized)
    ):
        return (
            "At Rostelecom, I kept SLA at 99.99% while load was growing 5-7% monthly."
            if english
            else "В Ростелекоме держал SLA 99,99% при росте нагрузки на 5-7% в месяц."
        )

    if any(
        marker in normalized
        for marker in ("стейкхолдер", "stakeholder", "бизнес", "business", "заказчик")
    ):
        return (
            "At Moscow Exchange, I rebuilt the business-IT interaction process and reduced business escalations to zero."
            if english
            else "На Московской Бирже выстроил взаимодействие бизнеса и IT и свёл бизнес-эскалации к нулю."
        )

    if any(
        marker in normalized
        for marker in ("quality", "качество", "predictability", "предсказуем")
    ):
        return (
            "At beeline, the share of successfully delivered projects increased from 75% to 92%."
            if english
            else "В билайне долю успешно реализованных проектов удалось поднять с 75% до 92%."
        )

    if any(
        marker in normalized
        for marker in (
            "delivery",
            "lifecycle",
            "жизненн",
            "релиз",
            "release",
            "risk",
            "риск",
            "project",
            "проект",
        )
    ):
        return (
            "At MTS, I reduced Time-to-Market from about 100 to 24 days."
            if english
            else "В МТС сократил Time-to-Market примерно со 100 до 24 дней."
        )

    return None


def _legacy_focus_sentences(vacancy_description: str, *, english: bool) -> list[str]:
    normalized = _normalize(vacancy_description)
    result: list[str] = []

    if any(marker in normalized for marker in ("интеграц", "integration", "api", "bss", "oss")):
        result.append(
            "I have led integration and platform projects involving APIs, BSS/OSS and high-load systems."
            if english
            else "Вёл интеграционные и платформенные проекты, в том числе с API, BSS/OSS и highload."
        )

    if any(
        marker in normalized
        for marker in ("roadmap", "приорит", "product", "продукт", "business", "бизнес")
    ):
        result.append(
            "I have worked at the business-IT boundary with requirements, prioritization, roadmaps and delivery."
            if english
            else "Работал на стыке бизнеса и IT: требования, приоритизация, roadmap и delivery."
        )

    if any(
        marker in normalized
        for marker in ("срок", "risk", "риск", "budget", "бюдж", "resource", "ресурс")
    ):
        result.append(
            "I have owned timelines, risks, dependencies, resources and cross-functional coordination."
            if english
            else "Отвечал за сроки, риски, зависимости, ресурсы и координацию кросс-функциональных команд."
        )

    if not result:
        result.append(
            "I have led IT projects end to end, from requirements and planning through release, production and further development."
            if english
            else "Вёл IT-проекты полного цикла - от требований и планирования до релиза, production и дальнейшего развития."
        )

    return result[:2]


def build_legacy_vacancy_cover_letter(
    *,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    stored_text: str | None,
    strengths: object = None,
) -> str:
    """Build a concise human legacy/OLD cover letter.

    Vacancy identity is intentionally not printed in the letter. The binding is
    structural through ApplicationDecisionSnapshot.application_id/vacancy_id.
    """
    language_sample = " ".join(
        [vacancy_title or "", vacancy_description or "", stored_text or ""]
    )
    cyr = len(re.findall(r"[А-Яа-яЁё]", language_sample))
    lat = len(re.findall(r"[A-Za-z]", language_sample))
    english = lat > cyr

    variant = _stable_variant(vacancy_title, vacancy_company, count=3)
    if english:
        openings = (
            "The responsibilities are close to the kind of IT delivery I have been leading.",
            "A lot of the responsibilities are familiar from the projects I have led.",
            "My background is close to this kind of end-to-end IT project work.",
        )
        closings = (
            "Happy to discuss the role and relevant projects in more detail.",
            "I would be glad to talk through the relevant experience.",
            "Happy to share more detail on similar projects.",
        )
        parts = ["Hello!", "", openings[variant]]
    else:
        openings = (
            "По описанию задач у меня есть близкий опыт.",
            "Судя по задачам, это довольно знакомый для меня контур.",
            "У меня есть релевантный опыт для такого типа IT-проектов.",
        )
        closings = (
            "Буду рад обсудить задачи подробнее.",
            "Если мой опыт подходит, буду рад пообщаться о задачах.",
            "Буду рад рассказать подробнее о похожих проектах.",
        )
        parts = ["Здравствуйте!", "", openings[variant]]

    parts.extend(_legacy_focus_sentences(vacancy_description, english=english))

    result_fact = _cover_result_fact(
        " ".join([vacancy_title or "", vacancy_description or ""]),
        english=english,
    )
    if result_fact:
        parts.append(result_fact)

    keep_ai_project = AI_PROJECT_URL in (stored_text or "")
    if keep_ai_project:
        parts.append(
            (
                "I also develop my own AI-agent project for automating the vacancy workflow: "
                if english
                else "Также развиваю собственный AI-agent для автоматизации работы с вакансиями: "
            )
            + AI_PROJECT_URL
        )

    parts.extend(["", closings[variant], "Aleksandr Rudenko" if english else "Александр Руденко"])
    return "\n".join(parts).strip()


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
        and item.get("match_quality") == "full"
        and item.get("category") != "education_clearance"
    ]

    specific_markers = (
        "delivery",
        "жизненн",
        "срок",
        "риск",
        "risk",
        "зависим",
        "stakeholder",
        "стейкхолдер",
        "требован",
        "requirement",
        "релиз",
        "release",
        "интеграц",
        "integration",
        "api",
        "телеком",
        "telecom",
        "bss",
        "oss",
        "agile",
        "scrum",
        "kanban",
        "подряд",
        "vendor",
        "budget",
        "бюдж",
        "ai",
        "ml",
        "llm",
        "security",
        "информационн",
        "иб",
        "jira",
        "confluence",
        "team",
        "команд",
        "decomposition",
        "декомпоз",
    )

    def rank(item: dict) -> tuple[int, int, int]:
        source = _normalize(str(item.get("source_text") or ""))
        generic_tenure = int(
            bool(
                re.search(
                    r"(?:опыт|experience).{0,80}\b\d+\s*"
                    r"(?:(?:[-–—]?\s*[а-яa-z]{1,3})\s+)?"
                    r"(?:лет|год|years?)",
                    source,
                )
            )
            and not any(marker in source for marker in specific_markers)
        )
        criticality = {
            "non_negotiable": 0,
            "core": 1,
            "preferred": 2,
        }.get(str(item.get("criticality") or ""), 3)
        specificity = sum(1 for marker in specific_markers if marker in source)
        return generic_tenure, criticality, -specificity

    ranked = [
        item
        for _, item in sorted(
            enumerate(requirements),
            key=lambda pair: (
                rank(pair[1])[0],
                rank(pair[1])[1],
                pair[0],
            ),
        )
    ]

    result: list[dict] = []
    seen: set[str] = set()
    for item in ranked:
        source_text = _clean_requirement_text(item.get("source_text"))
        normalized = _normalize(source_text)
        if not source_text or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
        if len(result) >= 2:
            break
    return result


def _clean_human_evidence(item: dict, *, english: bool) -> str:
    source = str(item.get("source_text") or "")
    evidence = str(item.get("candidate_evidence") or "")
    source_normalized = _normalize(source)
    normalized = _normalize(source + " " + evidence)

    # Prefer what the employer actually asks for over incidental words inside
    # a broad candidate-evidence string. This keeps the visible letter focused.
    if (
        "security" in source_normalized
        or ("информационн" in source_normalized and "безопас" in source_normalized)
        or re.search(r"\bиб\b", source_normalized)
    ):
        return (
            "In Moscow City IT, MTS and Rostelecom I coordinated information-security work within delivery: security requirements, audits, penetration tests and technical approvals."
            if english
            else "В ДИТ Москвы, МТС и Ростелекоме координировал ИБ в delivery: требования безопасности, аудиты и пентесты, согласование технических решений."
        )

    if any(
        marker in source_normalized
        for marker in (
            "technical",
            "техническ",
            "highload",
            "system design",
            "infrastructure",
            "инфраструктур",
        )
    ):
        return (
            "I have worked with high-load systems, APIs, infrastructure and system-design context, collaborating with architects on solution design."
            if english
            else "Работал с highload-системами, API, инфраструктурным и архитектурным контекстом, участвовал в системном дизайне вместе с архитектором."
        )

    if any(
        marker in source_normalized
        for marker in ("stakeholder", "стейкхолдер", "бизнес", "business", "архитект")
    ):
        return (
            "I have coordinated business, engineering, architecture, security and external vendors within the same delivery stream."
            if english
            else "Синхронизировал бизнес, разработку, архитектуру, ИБ и подрядчиков в одном delivery-контуре."
        )

    if any(
        marker in source_normalized
        for marker in ("team", "команд", "decomposition", "декомпоз", "task setting", "roles")
    ):
        return (
            "I have decomposed work, assigned responsibilities and coordinated cross-functional delivery teams, including teams of 10+ people."
            if english
            else "Декомпозировал работу, распределял роли и зоны ответственности и координировал кросс-функциональные команды, в том числе 10+ человек."
        )

    if (
        "телеком" in source_normalized
        or "telecom" in source_normalized
        or re.search(r"\b(?:bss|oss)\b", source_normalized)
    ):
        return (
            "I spent several years in telecom at MTS, Rostelecom and beeline, including BSS/OSS and high-load systems."
            if english
            else "Несколько лет работал в телекоме - МТС, Ростелеком и билайн, в том числе с BSS/OSS и highload."
        )

    if any(
        marker in source_normalized
        for marker in ("release", "релиз", "uat", "пси", "приемк", "тестир", "production", "эксплуатац")
    ):
        return (
            "I have run testing and acceptance, release planning and production rollouts."
            if english
            else "Организовывал тестирование и приёмку, управлял релизами и выводом изменений в production."
        )

    if any(marker in normalized for marker in ("jira", "confluence", "youtrack", "ms project")):
        return (
            "I work with Jira, Confluence, YouTrack and MS Project for planning, task tracking and project documentation."
            if english
            else "Работал с Jira, Confluence, YouTrack и MS Project для планирования, постановки задач и проектной документации."
        )

    if any(
        marker in source_normalized
        for marker in ("rfp", "rfq", "vendor", "подряд", "закуп", "договор", "contract")
    ):
        return (
            "I have managed vendors and procurement: RFP/RFQ, contracts, timelines, quality and acceptance."
            if english
            else "Управлял подрядчиками и закупками: RFP/RFQ, договоры, сроки, качество и приёмка."
        )

    if any(
        marker in source_normalized
        for marker in ("интеграц", "integration", "rest", "graphql", "grpc", "api")
    ):
        return (
            "I have led integration work with REST APIs, GraphQL and gRPC and participated in system design with architects."
            if english
            else "Вёл интеграционные проекты с REST API, GraphQL и gRPC, участвовал в системном дизайне вместе с архитектором."
        )

    if any(
        marker in source_normalized
        for marker in ("требован", "requirement", "тз", "backlog", "roadmap")
    ):
        return (
            "I have gathered and structured requirements, written specifications, decomposed work and managed backlogs and roadmaps."
            if english
            else "Сам собирал и структурировал требования, писал ТЗ, декомпозировал задачи и вёл backlog/roadmap."
        )

    if any(
        marker in source_normalized
        for marker in ("risk", "риск", "зависим", "budget", "бюдж", "resource", "ресурс", "срок")
    ):
        return (
            "I have owned timelines, risks, dependencies, resources and project budgets."
            if english
            else "Отвечал за сроки, риски, зависимости, ресурсы и бюджет проекта."
        )

    if any(marker in source_normalized for marker in ("agile", "scrum", "less", "kanban", "waterfall")):
        return (
            "I have worked with Scrum/LeSS, Kanban and Waterfall and adapted the process to the project context."
            if english
            else "Работал со Scrum/LeSS, Kanban и Waterfall и подбирал процесс под конкретный проект."
        )

    if any(
        marker in source_normalized
        for marker in (
            "full lifecycle",
            "full-cycle",
            "end-to-end",
            "end to end",
            "жизненн",
            "delivery",
            "полного цикла",
            "всех этап",
            "all stages",
            "от инициац",
            "до закрыт",
        )
    ):
        return (
            "At MTS, Rostelecom and Moscow City IT, I led IT projects end to end from requirements and planning to release, production and operations."
            if english
            else "В МТС, Ростелекоме и ДИТ Москвы вёл IT-проекты полного цикла - от требований и планирования до релиза, production и эксплуатации."
        )

    if any(
        marker in normalized
        for marker in (
            "technical expertise",
            "техническ",
            "highload",
            "system design",
            "infrastructure",
            "инфраструктур",
        )
    ):
        return (
            "I have worked with high-load systems, APIs, infrastructure and system-design context, collaborating with architects on solution design."
            if english
            else "Работал с highload-системами, API, инфраструктурным и архитектурным контекстом, участвовал в системном дизайне вместе с архитектором."
        )

    if re.search(r"(?:13\+|\b\d+\s*(?:years?|лет))", normalized):
        return (
            "I have 13+ years in IT, with the recent years focused on end-to-end project and delivery management."
            if english
            else "У меня 13+ лет в IT, последние годы - управление сложными IT-проектами и delivery полного цикла."
        )

    # Last-resort mappings can use the candidate evidence when the employer's
    # wording is too generic to classify on its own.
    if (
        "телеком" in normalized
        or "telecom" in normalized
        or re.search(r"\b(?:bss|oss)\b", normalized)
    ):
        return (
            "I spent several years in telecom at MTS, Rostelecom and beeline, including BSS/OSS and high-load systems."
            if english
            else "Несколько лет работал в телекоме - МТС, Ростелеком и билайн, в том числе с BSS/OSS и highload."
        )

    return (
        "I have relevant hands-on project-management experience in this area."
        if english
        else "С этим контуром работал на практике в нескольких IT-проектах."
    )


def _clean_cover_is_ai_relevant(
    *,
    vacancy_title: str,
    vacancy_description: str,
    extraction: dict,
) -> bool:
    # AI must be part of the role/requirements, not just a stray word anywhere
    # in the long vacancy description.
    title_match = bool(_CLEAN_AI_RE.search(vacancy_title or ""))
    requirement_match = any(
        _CLEAN_AI_RE.search(str(item.get("source_text") or ""))
        for item in (extraction.get("requirements") or [])
        if isinstance(item, dict)
    )
    return title_match or requirement_match


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
    """Build a short human CLEAN cover letter from grounded CLEAN evidence.

    The visible letter deliberately does not echo the full vacancy title or
    company name. Vacancy binding is structural in the application snapshot.
    Only full recruiter-visible matches are surfaced.
    """
    extraction = _clean_extraction_payload(extraction_json)
    matched = _clean_cover_requirements(extraction)

    language_sample = " ".join([vacancy_title or "", vacancy_description or ""])
    cyr = len(re.findall(r"[А-Яа-яЁё]", language_sample))
    lat = len(re.findall(r"[A-Za-z]", language_sample))
    english = lat > cyr

    ai_relevant = _clean_cover_is_ai_relevant(
        vacancy_title=vacancy_title,
        vacancy_description=vacancy_description,
        extraction=extraction,
    )
    ai_visible_fact = _clean_cover_ai_visible_fact(extraction)

    evidence_sentences: list[str] = []
    for item in matched:
        sentence = _clean_human_evidence(item, english=english)
        if sentence and sentence not in evidence_sentences:
            evidence_sentences.append(sentence)

    variant = _stable_variant(vacancy_title, vacancy_company, count=3)
    if english:
        openings = (
            "The responsibilities are close to the kind of IT delivery I have been leading.",
            "A lot of the responsibilities are familiar from the projects I have led.",
            "My background is close to this kind of end-to-end IT project work.",
        )
        closings = (
            "Happy to discuss the role and relevant projects in more detail.",
            "I would be glad to talk through the relevant experience.",
            "Happy to share more detail on similar projects.",
        )
        parts = ["Hello!", "", openings[variant]]
    else:
        openings = (
            "По описанию задач у меня есть близкий опыт.",
            "Судя по задачам, это довольно знакомый для меня контур.",
            "У меня есть релевантный опыт для такого типа IT-проектов.",
        )
        closings = (
            "Буду рад обсудить задачи подробнее.",
            "Если мой опыт подходит, буду рад пообщаться о задачах.",
            "Буду рад рассказать подробнее о похожих проектах.",
        )
        parts = ["Здравствуйте!", "", openings[variant]]

    if evidence_sentences:
        parts.extend(evidence_sentences[:2])
    else:
        parts.append(
            "I have led IT projects end to end, from requirements and planning through release, production and further development."
            if english
            else "Вёл IT-проекты полного цикла - от требований и планирования до релиза, production и дальнейшего развития."
        )

    result_fact = _cover_result_fact(
        " ".join(
            [
                vacancy_title or "",
                " ".join(str(item.get("source_text") or "") for item in matched),
            ]
        ),
        english=english,
    )
    if result_fact:
        parts.append(result_fact)

    if ai_relevant:
        if english:
            if ai_visible_fact:
                parts.append("In an AI context, I implemented AutoFAQ for documentation Q&A.")
            if _ai_project_cover_enabled():
                parts.append(
                    "I also develop my own AI-agent for automating the vacancy workflow: "
                    + AI_PROJECT_URL
                )
        else:
            if ai_visible_fact:
                parts.append("В AI-контексте внедрял AutoFAQ для Q&A по документации.")
            if _ai_project_cover_enabled():
                parts.append(
                    "Также развиваю собственный AI-agent для автоматизации работы с вакансиями: "
                    + AI_PROJECT_URL
                )

    parts.extend(["", closings[variant], "Aleksandr Rudenko" if english else "Александр Руденко"])
    return "\n".join(parts).strip()
