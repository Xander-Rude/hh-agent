import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from app.llm import LLMProvider
from app.resume_matcher import ResumeMatchDecision


MASTER_RESUME_PATH = Path("data/master_resume.yaml")
OUTPUT_DIR = Path("data/generated_resumes")

MAX_LLM_ATTEMPTS = 3
RESUME_TAILOR_VERSION = "v9"


# ============================================================
# MODELS
# ============================================================

class GeneratedBullet(BaseModel):
    text: str
    source_fact_ids: list[str] = Field(min_length=1, max_length=3)


class GeneratedExperience(BaseModel):
    experience_id: str
    bullets: list[GeneratedBullet] = Field(min_length=1, max_length=6)


class TailoredResume(BaseModel):
    target_title: str
    summary: str
    summary_fact_ids: list[str] = Field(min_length=1, max_length=12)
    key_skills: list[str]
    experience: list[GeneratedExperience]
    matched_requirements: list[str]
    unsupported_requirements: list[str]
    tailoring_notes: list[str]


class FactCheckResult(BaseModel):
    valid: bool
    unsupported_claims: list[str]
    suspicious_claims: list[str]
    notes: list[str]


class SummaryResult(BaseModel):
    summary: str
    source_fact_numbers: list[int] = Field(min_length=1, max_length=12)


class ExperienceBulletResult(BaseModel):
    text: str
    source_fact_numbers: list[int] = Field(min_length=1, max_length=3)


class ExperienceResult(BaseModel):
    bullets: list[ExperienceBulletResult] = Field(min_length=1, max_length=6)


class ExperienceSelectionResult(BaseModel):
    selected_fact_numbers: list[int] = Field(min_length=2, max_length=5)


class MetaResult(BaseModel):
    key_skills: list[str]
    matched_requirements: list[str]
    unsupported_requirements: list[str]
    tailoring_notes: list[str]


class AtomicRequirement(BaseModel):
    text: str
    kind: Literal["general", "specialized"] = "general"


class RequirementListResult(BaseModel):
    requirements: list[AtomicRequirement] = Field(min_length=1, max_length=32)


class RequirementMatchResult(BaseModel):
    matched: bool
    evidence_numbers: list[int] = Field(default_factory=list, max_length=8)


SOFT_REQUIREMENT_MARKERS = [
    # classic soft skills
    "коммуникативн",
    "умение договар",
    "переговор",
    "самостоятельност",
    "ответственност",
    "ориентац на результат",
    "ориентация на результат",
    "ориентации на результат",
    "проактивност",
    "инициативност",
    "стрессоустойчив",
    "лидерск",
    "настойчивост",
    "работоспособност",

    # cognitive / behavioral formulations that a CV cannot prove reliably
    "системное мышление",
    "быстро погружаться",
    "быстрое погружение",
    "сложный технический контекст",
    "сложный продуктовый контекст",
    "критически оценивать",
    "критическая оценка",
    "работа в условиях неопределенности",
    "работа в условиях неопределённости",
    "эффективная работа в условиях неопределенности",
    "эффективная работа в условиях неопределённости",
    "условиях неопределенности",
    "условиях неопределённости",
    "способность понимать зависимости",
    "понимание зависимостей",
    "фасилитац",

    # English variants
    "communication skills",
    "negotiation skills",
    "independent",
    "responsibility",
    "result oriented",
    "proactive",
    "system thinking",
    "systems thinking",
    "facilitation",
    "ambiguity",
    "resilience",
    "perseverance",
]


def is_soft_requirement(text: str) -> bool:
    normalized = normalize_text(text)

    return any(
        normalize_text(marker) in normalized
        for marker in SOFT_REQUIREMENT_MARKERS
    )


class SkillSelectionResult(BaseModel):
    selected_skills: list[str] = Field(min_length=8, max_length=18)


# Tiny fact-check schemas: intentionally NO free-text fields.
# Gemma only has to return booleans, which is far more reliable.
class SummaryAudit(BaseModel):
    supported: bool
    suspicious: bool


class BlockAudit(BaseModel):
    supported: list[bool]
    suspicious: list[bool]


# ============================================================
# MASTER DATA
# ============================================================

def load_master_resume() -> dict:
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(f"Не найден {MASTER_RESUME_PATH}")

    with MASTER_RESUME_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def get_experience_map(master: dict) -> dict:
    return {
        item["id"]: item
        for item in master.get("experiences", [])
    }


def get_global_fact_map(master: dict) -> dict:
    return {
        item["id"]: item["text"]
        for item in master.get("global_facts", [])
    }


def get_all_fact_map(master: dict) -> dict:
    result = get_global_fact_map(master)

    for experience in master.get("experiences", []):
        for fact in experience.get("facts", []):
            result[fact["id"]] = fact["text"]

    return result


# ============================================================
# LLM HELPERS
# ============================================================

def extract_response_text(response) -> str:
    if hasattr(response, "message"):
        message = response.message
        if hasattr(message, "content"):
            return message.content or ""

    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""

    raise RuntimeError("Не удалось получить текст из ответа Ollama.")


def extract_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```$", "", text)
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("LLM не вернула JSON.")

    return json.loads(text[start:end + 1])


def ask_structured(prompt: str, model_cls, attempts: int = MAX_LLM_ATTEMPTS):
    """
    Structured output + retry.
    Used by generation and auditing.
    """
    schema = model_cls.model_json_schema()
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            llm = LLMProvider()

            response = llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                format_schema=schema,
            )

            raw = extract_response_text(response).strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = extract_json(raw)

            return model_cls.model_validate(data)

        except Exception as exc:
            last_error = exc
            print(
                f"[LLM] structured attempt {attempt}/{attempts} failed: {exc}"
            )

    raise RuntimeError(
        f"LLM structured output failed after {attempts} attempts: {last_error}"
    )


# ============================================================
# BASIC TEXT
# ============================================================

def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    value = value.lower()
    value = value.replace("ё", "е")
    value = value.replace("—", "-")
    value = value.replace("–", "-")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def make_safe_filename(title: str) -> str:
    title = re.sub(r'[<>:"/\\|?*]', "_", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:100]


# ============================================================
# LANGUAGE
# ============================================================

def detect_vacancy_language(
    title: str,
    description: str,
) -> Literal["ru", "en"]:
    text = title + "\n" + description[:6000]

    latin = len(re.findall(r"[A-Za-z]", text))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))

    return "ru" if cyrillic >= latin else "en"


def validate_language(text: str, language: str) -> bool:
    latin = len(re.findall(r"[A-Za-z]", text))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))

    if language == "ru":
        if cyrillic < 100:
            return False
        if cyrillic > 0 and latin / cyrillic > 0.75:
            return False

    if language == "en" and latin < 100:
        return False

    return True


# ============================================================
# TITLE / EXPERIENCE SELECTION
# ============================================================

ROLE_EXPERIENCE_PRIORITY = {
    "project": [
        "dit_2025",
        "beeline_bss_2024",
        "moex_2023",
        "mts_2021",
        "rostelecom_2018",
    ],
    "delivery": [
        "beeline_bss_2024",
        "dit_2025",
        "moex_2023",
        "mts_2021",
        "rostelecom_2018",
    ],
    "technical_project": [
        "dit_2025",
        "mts_2021",
        "rostelecom_2018",
        "beeline_bss_2024",
        "moex_2023",
    ],
    "product": [
        "mts_2021",
        "rostelecom_2018",
        "ertelecom_2014",
        "dit_2025",
        "moex_2023",
    ],
}


SUMMARY_FACTS_BY_ROLE = {
    "project": [
        "total_experience",
        "portfolio_30",
        "budget_350",
        "team_70",
        "c_level",
        "full_cycle",
        "delivery",
        "roadmap",
        "requirements",
    ],
    "delivery": [
        "total_experience",
        "portfolio_30",
        "budget_350",
        "team_70",
        "c_level",
        "delivery",
        "pmo",
    ],
    "technical_project": [
        "total_experience",
        "team_70",
        "hired_40",
        "engineering_management",
        "delivery",
        "highload",
        "architecture_context",
        "sla",
    ],
    "product": [
        "total_experience",
        "team_70",
        "product_cycle",
        "roadmap",
        "requirements",
        "backlog",
        "pnl",
        "oss_bss",
    ],
}


def get_safe_target_title(
    decision: ResumeMatchDecision,
    master: dict,
) -> str:
    if decision.action == "tailor_existing":
        key = decision.selected_resume_key or ""

        family = (
            master
            .get("target_role_families", {})
            .get(key, {})
        )

        safe_title = family.get("safe_title")

        if safe_title:
            return safe_title

        if decision.selected_resume_title:
            return decision.selected_resume_title

    return (
        decision.target_title
        or decision.selected_resume_title
        or "IT Project Manager"
    )


def localize_target_title(
    title: str,
    language: str,
) -> str:
    """
    For bilingual safe titles like:
    Senior Project Manager / Руководитель IT-проектов

    RU vacancy -> Руководитель IT-проектов
    EN vacancy -> Senior Project Manager
    """
    if "/" not in title:
        return title.strip()

    parts = [
        part.strip()
        for part in title.split("/")
        if part.strip()
    ]

    if not parts:
        return title.strip()

    if language == "ru":
        cyrillic_parts = [
            part
            for part in parts
            if re.search(r"[А-Яа-яЁё]", part)
        ]
        if cyrillic_parts:
            return cyrillic_parts[-1]

    if language == "en":
        latin_parts = [
            part
            for part in parts
            if re.search(r"[A-Za-z]", part)
        ]
        if latin_parts:
            return latin_parts[0]

    return title.strip()


def choose_experience_ids(
    decision: ResumeMatchDecision,
    master: dict,
) -> list[str]:
    key = decision.selected_resume_key or "project"

    priority = ROLE_EXPERIENCE_PRIORITY.get(
        key,
        ROLE_EXPERIENCE_PRIORITY["project"],
    )

    experience_map = get_experience_map(master)

    return [
        experience_id
        for experience_id in priority
        if experience_id in experience_map
    ]


# ============================================================
# SUMMARY GENERATION
# ============================================================

def generate_summary(
    vacancy_title: str,
    vacancy_description: str,
    decision: ResumeMatchDecision,
    master: dict,
    language: str,
) -> tuple[str, list[str]]:
    """
    Deterministic summary.

    We intentionally do NOT let the LLM write the summary anymore:
    the previous version invented "risk management" and the LLM auditor
    incorrectly accepted it.

    Summary is assembled only from whitelisted global facts.
    """
    role_key = decision.selected_resume_key or "project"
    facts = get_global_fact_map(master)

    if language == "ru":
        if role_key == "delivery":
            fact_ids = [
                "total_experience",
                "portfolio_30",
                "budget_350",
                "team_70",
                "delivery",
                "pmo",
                "c_level",
            ]
            summary = (
                "Более 13 лет опыта в IT и управлении проектами и программами. "
                "Управлял портфелем из 30+ проектов, бюджетом около 350 млн рублей "
                "и кросс-функциональными командами до 70 сотрудников. "
                "Имею подтвержденный опыт Delivery Management, PMO и развития процессов "
                "проектного управления, включая взаимодействие с C-level и стейкхолдерами уровня CEO-1."
            )

        elif role_key == "technical_project":
            fact_ids = [
                "total_experience",
                "team_70",
                "hired_40",
                "engineering_management",
                "delivery",
                "highload",
                "architecture_context",
                "sla",
            ]
            summary = (
                "Более 13 лет опыта в IT, включая управление техническими и высоконагруженными проектами. "
                "Управлял кросс-функциональными командами до 70 сотрудников и нанял 40+ специалистов. "
                "Имею подтвержденный опыт Engineering Management, Delivery Management, "
                "работы с архитектурным контекстом, интеграциями, эксплуатацией и SLA."
            )

        elif role_key == "product":
            fact_ids = [
                "total_experience",
                "team_70",
                "product_cycle",
                "roadmap",
                "requirements",
                "backlog",
                "pnl",
                "oss_bss",
            ]
            summary = (
                "Более 13 лет опыта в IT и развитии цифровых продуктов. "
                "Имею подтвержденный опыт полного цикла разработки продуктов, управления roadmap, "
                "бизнес- и IT-требованиями, backlog и приоритизацией через ICE Scoring. "
                "Работал с B2B-продуктами, P&L, OSS/BSS и кросс-функциональными командами до 70 сотрудников."
            )

        else:
            fact_ids = [
                "total_experience",
                "portfolio_30",
                "budget_350",
                "team_70",
                "full_cycle",
                "delivery",
                "roadmap",
                "requirements",
                "c_level",
            ]
            summary = (
                "Более 13 лет опыта в IT и управлении проектами. "
                "Управлял портфелем из 30+ проектов, бюджетом около 350 млн рублей "
                "и кросс-функциональными командами до 70 сотрудников. "
                "Имею подтвержденный опыт полного цикла IT-разработки, Delivery Management, "
                "управления roadmap, бизнес- и IT-требованиями и взаимодействия с C-level."
            )

    else:
        # Conservative English fallback, also fully deterministic.
        if role_key == "delivery":
            fact_ids = [
                "total_experience",
                "portfolio_30",
                "budget_350",
                "team_70",
                "delivery",
                "pmo",
                "c_level",
            ]
            summary = (
                "13+ years of experience in IT project and program management. "
                "Managed a portfolio of 30+ projects, a budget of approximately RUB 350M, "
                "and cross-functional teams of up to 70 people. "
                "Proven experience in Delivery Management, PMO, project-management process development, "
                "and C-level stakeholder management."
            )
        elif role_key == "technical_project":
            fact_ids = [
                "total_experience",
                "team_70",
                "hired_40",
                "engineering_management",
                "delivery",
                "highload",
                "architecture_context",
                "sla",
            ]
            summary = (
                "13+ years of IT experience, including technical and high-load projects. "
                "Managed cross-functional teams of up to 70 people and hired 40+ specialists. "
                "Proven experience in Engineering Management, Delivery Management, architecture context, "
                "integrations, operations, and SLA management."
            )
        elif role_key == "product":
            fact_ids = [
                "total_experience",
                "team_70",
                "product_cycle",
                "roadmap",
                "requirements",
                "backlog",
                "pnl",
                "oss_bss",
            ]
            summary = (
                "13+ years of experience in IT and digital product development. "
                "Proven experience across the full product lifecycle, roadmap and requirements management, "
                "backlog prioritization using ICE Scoring, B2B products, P&L, and OSS/BSS. "
                "Managed cross-functional teams of up to 70 people."
            )
        else:
            fact_ids = [
                "total_experience",
                "portfolio_30",
                "budget_350",
                "team_70",
                "full_cycle",
                "delivery",
                "roadmap",
                "requirements",
                "c_level",
            ]
            summary = (
                "13+ years of experience in IT and project management. "
                "Managed a portfolio of 30+ projects, a budget of approximately RUB 350M, "
                "and cross-functional teams of up to 70 people. "
                "Proven experience across the full IT delivery lifecycle, Delivery Management, "
                "roadmap and requirements management, and C-level stakeholder engagement."
            )

    existing_ids = [
        fact_id
        for fact_id in fact_ids
        if fact_id in facts
    ]

    if len(existing_ids) < 3:
        raise RuntimeError(
            "Недостаточно подтвержденных global facts для deterministic summary."
        )

    return summary, existing_ids


# ============================================================
# ONE EXPERIENCE BLOCK
# ============================================================

def generate_experience_block(
    experience: dict,
    vacancy_title: str,
    vacancy_description: str,
    language: str,
) -> GeneratedExperience:
    """
    The LLM DOES NOT WRITE resume bullets anymore.

    It only selects 2-5 source facts by local number.
    Python then inserts the original master fact text verbatim.

    This eliminates:
    - invented causal links;
    - strengthened claims;
    - "(1, 3, 5)" garbage in visible bullets;
    - paraphrase drift.
    """
    facts = experience.get("facts", [])

    if not facts:
        raise RuntimeError(
            f"Нет facts в experience {experience['id']}"
        )

    number_to_fact = {}
    available = []

    for number, fact in enumerate(facts, start=1):
        number_to_fact[number] = fact

        available.append(
            {
                "number": number,
                "text": fact["text"],
            }
        )

    prompt = f"""
Ты НЕ ПИШЕШЬ текст резюме.
Ты только выбираешь наиболее релевантные исходные факты
ОДНОГО места работы под вакансию.

Компания:
{experience["company"]}

Должность:
{experience["position"]}

Период:
{experience["period"]}

ВАКАНСИЯ:
{vacancy_title}

{vacancy_description[:9000]}

ДОСТУПНЫЕ ФАКТЫ:
{json.dumps(available, ensure_ascii=False, indent=2)}

Выбери от 2 до 5 наиболее релевантных фактов.

Правила:
- возвращай ТОЛЬКО номера из списка;
- не переписывай факты;
- не объединяй факты;
- не добавляй комментарии;
- ориентируйся на требования конкретной вакансии;
- более сильные и свежие достижения предпочитай общим формулировкам.

Верни структурированный результат.
"""

    result = ask_structured(
        prompt,
        ExperienceSelectionResult,
    )

    invalid = [
        number
        for number in result.selected_fact_numbers
        if number not in number_to_fact
    ]

    if invalid:
        raise RuntimeError(
            f"{experience['id']}: неизвестные номера фактов {invalid}"
        )

    # Preserve order, remove duplicates.
    selected_numbers = []
    seen = set()

    for number in result.selected_fact_numbers:
        if number not in seen:
            seen.add(number)
            selected_numbers.append(number)

    generated_bullets = []

    for number in selected_numbers:
        fact = number_to_fact[number]

        generated_bullets.append(
            GeneratedBullet(
                text=fact["text"],
                source_fact_ids=[fact["id"]],
            )
        )

    return GeneratedExperience(
        experience_id=experience["id"],
        bullets=generated_bullets,
    )


# ============================================================
# SKILLS / META
# ============================================================

def get_allowed_skills(
    master: dict,
    experience_ids: list[str],
) -> list[str]:
    experience_map = get_experience_map(master)

    result = []
    seen = set()

    for experience_id in experience_ids:
        experience = experience_map.get(experience_id)

        if not experience:
            continue

        for skill in experience.get("skills", []):
            key = normalize_text(skill)

            if key in seen:
                continue

            seen.add(key)
            result.append(skill)

    additional = [
        "Project Management",
        "Program Management",
        "Delivery Management",
        "Engineering Management",
        "Stakeholder Management",
        "Budget Management",
        "PMO",
        "Agile",
        "Scrum",
        "Kanban",
        "LeSS",
        "Waterfall",
        "Jira",
        "Confluence",
        "Highload",
        "OSS/BSS",
        "B2B",
        "P&L",
    ]

    for skill in additional:
        key = normalize_text(skill)

        if key not in seen:
            seen.add(key)
            result.append(skill)

    return result


def build_meta_evidence(
    master: dict,
    experience_ids: list[str] | None = None,
) -> list[str]:
    """
    Evidence for vacancy matching comes from the FULL confirmed career profile,
    not only from the subset of jobs selected for the targeted CV.

    This is intentional:
    - matching asks "does the candidate have this experience anywhere?";
    - tailoring asks "which experience should be shown in this specific CV?".

    Those are different questions and must not share the same evidence scope.
    """
    evidence = []

    for fact in master.get("global_facts", []):
        evidence.append(fact["text"])

    for experience in master.get("experiences", []):
        for fact in experience.get("facts", []):
            evidence.append(fact["text"])

    # Keep order but remove duplicates.
    result = []
    seen = set()

    for item in evidence:
        key = normalize_text(item)

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def _extract_requirement_pass(
    vacancy_title: str,
    vacancy_description: str,
    language: str,
    mode: Literal["qualifications", "responsibilities"],
) -> list[AtomicRequirement]:
    language_rule = (
        "Формулируй пункты по-русски."
        if language == "ru"
        else
        "Write the requirements in English."
    )

    if mode == "qualifications":
        task = """
Извлеки ТОЛЬКО требования к кандидату:
опыт, знания, методологии, тип команд, документация,
коммуникационные требования и другие qualification criteria.

НЕ извлекай обязанности роли, если они не сформулированы
как требование к кандидату.
"""
    else:
        task = """
Извлеки ТОЛЬКО ключевые обязанности / задачи роли:
что человек должен будет делать на этой позиции.

НЕ извлекай общие требования к кандидату вроде
"опыт 2+ лет", "Agile", "коммуникабельность",
если это не является обязанностью.
"""

    prompt = f"""
{task}

{language_rule}

ВАКАНСИЯ:
{vacancy_title}

{vacancy_description[:14000]}

КРИТИЧЕСКИЕ ПРАВИЛА:

1. Один пункт = ОДНО атомарное проверяемое требование/обязанность.

2. Разбивай составные пункты.

Например:
"Постановка задач команде
(разработчики, аналитики, NLP/ML, QA)"

раздели минимум на:
- "Постановка задач команде разработки"
- "Постановка задач аналитикам"
- "Опыт взаимодействия с NLP/ML-командой"
- "Постановка задач QA"

3. Специализированные домены/технологии выделяй отдельно:
NLP, ML, AI, LLM, chatbot, роботизация чатов,
конкретный cloud/database/framework/продуктовый домен.
Для них kind="specialized".

4. Общие PM-компетенции и обязанности — kind="general".

5. "Распределенные команды" выделяй отдельным пунктом.

6. Если один пункт содержит несколько независимых обязанностей,
ОБЯЗАТЕЛЬНО разделяй их.

Пример:
"Обеспечивать прозрачность сроков, зависимостей, рисков и обязательств"

раздели как минимум на:
- "Обеспечивать прозрачность сроков"
- "Управлять/отслеживать зависимости"
- "Выявлять/управлять рисками"
- "Контролировать обязательства"

7. Не придумывай то, чего нет в вакансии.

Верни структурированный результат.
"""

    result = ask_structured(
        prompt,
        RequirementListResult,
    )

    return result.requirements


def extract_vacancy_requirements(
    vacancy_title: str,
    vacancy_description: str,
    language: str,
) -> list[AtomicRequirement]:
    """
    Two independent passes prevent the model from dropping either:
    - candidate qualification requirements;
    - actual role responsibilities.
    """
    qualifications = _extract_requirement_pass(
        vacancy_title,
        vacancy_description,
        language,
        "qualifications",
    )

    responsibilities = _extract_requirement_pass(
        vacancy_title,
        vacancy_description,
        language,
        "responsibilities",
    )

    combined = qualifications + responsibilities

    deduped = []
    seen = set()

    for item in combined:
        clean = item.text.strip()
        key = normalize_text(clean)

        if not clean or key in seen:
            continue

        seen.add(key)

        deduped.append(
            AtomicRequirement(
                text=clean,
                kind=item.kind,
            )
        )

    return deduped[:30]


def _evidence_contains_any(
    evidence: list[str],
    markers: list[str],
) -> bool:
    corpus = normalize_text("\n".join(evidence))

    return any(
        normalize_text(marker) in corpus
        for marker in markers
    )


def deterministic_requirement_guard(
    requirement: AtomicRequirement,
    evidence: list[str],
) -> bool | None:
    """
    Hard guards for cases where semantic similarity is dangerous.

    True  -> definitely matched
    False -> definitely unsupported
    None  -> let the LLM classify semantic equivalence
    """
    req = normalize_text(requirement.text)

    # ------------------------------------------------------------------
    # POSITIVE CANONICAL MATCHES
    # ------------------------------------------------------------------
    # These are not guesses. They map vacancy wording to facts that are
    # explicitly present in master_resume.yaml / resume profiles.

    # IT project management / large projects.
    if (
        ("it-проект" in req or "it проект" in req or "it project" in req)
        and (
            "опыт" in req
            or "управлен" in req
            or "ведени" in req
            or "project manager" in req
            or "project lead" in req
        )
    ):
        return _evidence_contains_any(
            evidence,
            [
                "руководитель проектов",
                "управлял портфелем it-проектов",
                "управлял коммерческими и технологическими проектами",
                "13+ лет",
            ],
        )

    # Program management.
    if (
        "управлен" in req
        and "программ" in req
    ) or "program management" in req:
        return _evidence_contains_any(
            evidence,
            [
                "program manager",
                "program management",
                "портфелем проектов",
                "pmo",
            ],
        )

    # Portfolio management.
    if (
        "портфел" in req
        or "portfolio" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "портфелем проектов",
                "портфелем it-проектов",
                "30+",
                "portfolio",
            ],
        )

    # Senior / C-level stakeholders.
    if (
        "senior" in req
        or "c-level" in req
        or "руководител" in req
        and "уров" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "c-level",
                "ceo-1",
                "руководителями высшего звена",
            ],
        )

    # Agile / Scrum / Kanban / Waterfall are direct facts.
    methodology_markers = {
        "agile": ["agile"],
        "scrum": ["scrum"],
        "kanban": ["kanban"],
        "waterfall": ["waterfall"],
    }

    for marker, evidence_markers in methodology_markers.items():
        if marker in req:
            return _evidence_contains_any(
                evidence,
                evidence_markers,
            )

    # Requirements management / collection / formalization.
    if (
        "требован" in req
        or "requirements" in req
    ):
        # Technical documentation is handled by its stricter rule below.
        if not (
            "техническ" in req
            and "документац" in req
        ):
            return _evidence_contains_any(
                evidence,
                [
                    "управлял требованиями",
                    "бизнес-требованиями",
                    "бизнес- и it-требованиями",
                    "requirements",
                ],
            )

    # Backlog management / prioritization.
    if (
        "бэклог" in req
        or "backlog" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "управление backlog",
                "backlog через ice scoring",
                "backlog",
            ],
        )

    if (
        "приоритизац" in req
        or "prioritization" in req
        or "prioritisation" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "ice scoring",
                "приоритизац",
            ],
        )

    # Product-management approaches / product lifecycle.
    if (
        "управлен" in req
        and "продукт" in req
    ) or "product management" in req:
        return _evidence_contains_any(
            evidence,
            [
                "полный цикл разработки продуктов",
                "продуктовые процессы разработки",
                "roadmap",
                "p&l",
            ],
        )

    # Modern software development processes.
    if (
        "процесс" in req
        and "разработ" in req
    ) or "software development" in req:
        return _evidence_contains_any(
            evidence,
            [
                "продуктовые процессы разработки",
                "полный цикл разработки",
                "scrum less",
                "kanban",
            ],
        )

    # Roadmap consolidation / management.
    if "roadmap" in req:
        return _evidence_contains_any(
            evidence,
            [
                "управлял roadmap",
                "roadmap",
                "связал продуктовые и технические планы",
                "квартальное планирование",
            ],
        )

    # Project planning: timelines and resources.
    if (
        "планирован" in req
        and "срок" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "планирование",
                "time-to-market",
                "t2m",
                "срок",
            ],
        )

    if (
        "планирован" in req
        and "ресурс" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "управлял команд",
                "бюджет",
                "планирование",
            ],
        )

    # Multiple initiatives / multiple teams: require portfolio/team evidence.
    if (
        "нескольк" in req
        and (
            "инициатив" in req
            or "команд" in req
            or "проект" in req
        )
    ):
        return _evidence_contains_any(
            evidence,
            [
                "30+",
                "портфелем проектов",
                "командой руководителей проектов",
                "командами",
            ],
        )

    # Coordination of projects / initiatives.
    if (
        "координир" in req
        and (
            "проект" in req
            or "инициатив" in req
            or "команд" in req
        )
    ):
        return _evidence_contains_any(
            evidence,
            [
                "координировал разработку и эксплуатацию",
                "управлял портфелем проектов",
                "командой руководителей проектов",
            ],
        )

    # Explicit PM seniority/experience
    if (
        ("project manager" in req or "project lead" in req)
        and (
            "2" in req
            or "лет" in req
            or "years" in req
        )
    ):
        return _evidence_contains_any(
            evidence,
            [
                "13+ лет",
                "руководитель проектов",
                "управлял портфелем",
                "project management",
            ],
        )

    # Distributed teams are not equivalent to large/cross-functional teams.
    if (
        "распредел" in req
        or "distributed team" in req
        or "distributed teams" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "распределенн",
                "распределённ",
                "distributed",
                "географически распредел",
                "удален",
                "удалён",
            ],
        )

    # Specialized claims require direct source evidence.
    specialized_marker_groups = [
        ["nlp"],
        ["machine learning", "машинное обучение"],
        ["llm"],
        ["искусственный интеллект", "artificial intelligence"],
        ["чат-бот", "чатбот", "chatbot", "роботизац", "роботизации чат"],
    ]

    for markers in specialized_marker_groups:
        if any(
            normalize_text(marker) in req
            for marker in markers
        ):
            return _evidence_contains_any(
                evidence,
                markers,
            )

    # Task execution control / delivery coordination.
    if (
        "контрол" in req
        and "выполнен" in req
        and "задач" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "управлял delivery",
                "координировал разработку и эксплуатацию",
                "управлял портфелем проектов",
            ],
        )

    # Task-setting: only developers are directly confirmed.
    if "постанов" in req and "задач" in req:
        if (
            "аналит" in req
            or "qa" in req
            or "тестиров" in req
        ):
            return False

        if (
            "разработ" in req
            and _evidence_contains_any(
                evidence,
                [
                    "постановке задач разработчикам",
                    "постановка задач разработчикам",
                ],
            )
        ):
            return True

    # External stakeholders require direct evidence of external parties/partners.
    if (
        "внешн" in req
        and "стейкхолдер" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "внешними стейкхолдерами",
                "внешними заказчиками",
                "партнерами",
                "партнёрами",
                "строил отношения с партнерами",
                "строил отношения с партнёрами",
            ],
        )

    if (
        "внутрен" in req
        and "стейкхолдер" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "бизнес-заказчиками",
                "внутренними бизнес-заказчиками",
                "стейкхолдерами уровня ceo-1",
                "c-level",
            ],
        )

    # Risk/change management should not be inferred from generic PM experience.
    if "управлен" in req and "риск" in req:
        return _evidence_contains_any(
            evidence,
            [
                "управление рисками",
                "управлял рисками",
                "risk management",
            ],
        )

    if "управлен" in req and "изменен" in req:
        return _evidence_contains_any(
            evidence,
            [
                "управление изменениями",
                "управлял изменениями",
                "change management",
            ],
        )

    # Quality of implementation needs concrete delivery/testing evidence.
    if (
        "контрол" in req
        and "качеств" in req
        and (
            "внедрен" in req
            or "реализац" in req
            or "implementation" in req
        )
    ):
        return _evidence_contains_any(
            evidence,
            [
                "автоматизированного тестирования",
                "вывода функциональности в production",
                "sla платформы",
                "успешной реализации проектов",
            ],
        )

    # Acceptance requires direct evidence; testing can be supported only by explicit testing facts.
    if "приемк" in req or "приёмк" in req:
        return _evidence_contains_any(
            evidence,
            [
                "приемка",
                "приёмка",
                "acceptance",
            ],
        )

    if "тестирован" in req:
        return _evidence_contains_any(
            evidence,
            [
                "тестирован",
                "automated testing",
                "автоматизированного тестирования",
            ],
        )

    # Technical documentation needs direct evidence.
    if "техническ" in req and "документац" in req:
        return _evidence_contains_any(
            evidence,
            [
                "техническая документация",
                "технической документацией",
                "technical documentation",
                "документац",
            ],
        )

    # Risk-related responsibilities must have direct evidence.
    # Do NOT infer them from generic senior PM experience.
    if "риск" in req or "risk" in req:
        return _evidence_contains_any(
            evidence,
            [
                "управление рисками",
                "управлял рисками",
                "выявлял риски",
                "реестр рисков",
                "risk management",
                "project risks",
            ],
        )

    # Blocker removal is a concrete responsibility, not a generic PM synonym.
    if (
        "блокер" in req
        or "блокиров" in req
        or "снятие блок" in req
        or "remove blocker" in req
        or "removing blocker" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "снимал блокеры",
                "снятие блокеров",
                "устранял блокеры",
                "remove blockers",
                "removed blockers",
            ],
        )

    # Agreements / commitments: require direct evidence.
    if (
        "договоренност" in req
        or "обязательств" in req
        or "commitment" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "контроль договоренностей",
                "контроль договорённостей",
                "управление обязательствами",
                "commitment management",
            ],
        )

    # Dependency management must not be inferred from roadmap/program experience.
    if (
        "зависимост" in req
        or "dependency" in req
        or "dependencies" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "управление зависимостями",
                "межпроектными зависимостями",
                "dependency management",
                "project dependencies",
            ],
        )

    # Facilitation is not equivalent to stakeholder management.
    if "фасилитац" in req or "facilitation" in req:
        return None

    # Conflict resolution CAN be matched, but only from the direct MTS fact.
    if (
        "разрешен" in req and "конфликт" in req
        or "разрешён" in req and "конфликт" in req
        or "conflict resolution" in req
    ):
        return _evidence_contains_any(
            evidence,
            [
                "регулировал конфликты",
                "разрешал конфликты",
                "conflict resolution",
            ],
        )

    return None


def classify_requirement(
    requirement: AtomicRequirement,
    evidence: list[str],
) -> tuple[bool, list[int]]:
    """
    Classify ONE requirement in isolation.
    Specialized domain/technology claims require direct evidence.
    Generic PM competencies can use semantic equivalents.
    """
    guard = deterministic_requirement_guard(
        requirement,
        evidence,
    )

    if guard is not None:
        if guard:
            return True, [1] if evidence else []
        return False, []

    numbered_evidence = [
        {
            "number": index,
            "text": item,
        }
        for index, item in enumerate(
            evidence,
            start=1,
        )
    ]

    prompt = f"""
Проверь ОДНО АТОМАРНОЕ требование вакансии
против подтвержденного опыта кандидата.

ТРЕБОВАНИЕ:
{requirement.text}

ТИП:
{requirement.kind}

ПОДТВЕРЖДЕННЫЙ ОПЫТ:
{json.dumps(
    numbered_evidence,
    ensure_ascii=False,
    indent=2,
)}

Правила классификации:

1. matched=true только если опыт подтверждает требование
   напрямую или достаточно конкретным эквивалентом по смыслу.

КРИТИЧЕСКИ:
если связь строится только на предположении
"сильный/старший Project Manager наверняка это умеет",
ставь matched=false.

При этом обычные PM-компетенции можно считать подтвержденными
семантически эквивалентными фактами:
project/program/portfolio management, Agile/Scrum/Kanban,
requirements, roadmap, backlog, prioritization, delivery,
stakeholders, planning.

Нельзя автоматически засчитывать узкие или поведенческие вещи:
фасилитацию, системное мышление, неопределенность,
риски, блокеры, договоренности, конкретные технологии
без прямого source evidence.

2. Для ОБЩИХ project-management требований
   допускается переносимый эквивалентный опыт.

Примеры:
- "сбор и формализация требований"
  подтверждается фактом управления бизнес- и IT-требованиями;
- "управление бэклогом / приоритизация"
  подтверждается backlog + ICE Scoring;
- "коммуникация со стейкхолдерами"
  подтверждается C-level / CEO-1 / бизнес-заказчиками;
- "Project Manager от 2 лет"
  подтверждается 13+ годами релевантного опыта;
- "постановка задач разработчикам"
  подтверждается только если есть прямой факт о постановке задач
  или эквивалентная ответственность за цикл разработки.

3. Если TYPE="specialized", нужен ПРЯМОЙ факт.
Семантической похожести недостаточно.

Например:
- роботизация чатов;
- chatbot;
- NLP;
- ML;
- LLM;
- конкретный облачный стек;
- конкретная отрасль/продукт.

Нельзя считать специализированное требование подтвержденным
только потому, что кандидат в целом сильный Project Manager.

4. "Распределенная команда" НЕ эквивалентна
"кросс-функциональной команде", "команде 70 человек"
или обычному управлению командой.

5. Если требование упоминает NLP/ML-команду,
оно не подтверждено обычным опытом постановки задач разработчикам.

6. evidence_numbers:
   - если matched=true, укажи номера подтверждающих фактов;
   - если matched=false, верни пустой список;
   - используй только существующие номера.

Верни структурированный результат.
"""

    result = ask_structured(
        prompt,
        RequirementMatchResult,
    )

    valid_numbers = set(
        range(
            1,
            len(evidence) + 1,
        )
    )

    evidence_numbers = [
        number
        for number in result.evidence_numbers
        if number in valid_numbers
    ]

    if result.matched and not evidence_numbers:
        # A "matched" result without any evidence is not trusted.
        return False, []

    return (
        result.matched,
        evidence_numbers,
    )


def select_key_skills(
    vacancy_title: str,
    vacancy_description: str,
    allowed_skills: list[str],
    language: str,
) -> list[str]:
    language_rule = (
        "Учитывай русскоязычную вакансию."
        if language == "ru"
        else
        "Consider the English-language vacancy."
    )

    prompt = f"""
Выбери наиболее релевантные навыки кандидата под вакансию.

{language_rule}

ВАКАНСИЯ:
{vacancy_title}

{vacancy_description[:9000]}

РАЗРЕШЕННЫЕ НАВЫКИ:
{json.dumps(
    allowed_skills,
    ensure_ascii=False,
    indent=2,
)}

Правила:
- выбери 10-18 навыков;
- только точные элементы из разрешенного списка;
- приоритет навыкам, которые реально помогают в этой вакансии;
- не добавляй новые навыки;
- не дублируй близкие навыки без необходимости.

Верни структурированный результат.
"""

    result = ask_structured(
        prompt,
        SkillSelectionResult,
    )

    allowed_map = {
        normalize_text(skill): skill
        for skill in allowed_skills
    }

    selected = []
    seen = set()

    for skill in result.selected_skills:
        key = normalize_text(skill)

        if key not in allowed_map:
            continue

        canonical = allowed_map[key]

        if key in seen:
            continue

        seen.add(key)
        selected.append(canonical)

    if len(selected) < 8:
        # deterministic fallback
        for skill in allowed_skills:
            key = normalize_text(skill)

            if key in seen:
                continue

            selected.append(skill)
            seen.add(key)

            if len(selected) >= 10:
                break

    return selected[:18]


def generate_meta(
    vacancy_title: str,
    vacancy_description: str,
    master: dict,
    experience_ids: list[str],
    language: str,
) -> MetaResult:
    """
    Robust staged meta generation:

    1. Extract actual vacancy requirements.
    2. Classify EACH requirement independently against candidate evidence.
    3. Select key skills only from whitelist.
    4. Build tailoring notes in Python.

    This prevents the previous inversion where
    "Project Manager 2+ years" became unsupported while
    "full chatbot automation project" became matched.
    """
    allowed_skills = get_allowed_skills(
        master,
        experience_ids,
    )

    evidence = build_meta_evidence(
        master,
    )

    print("[TAILOR] Извлекаю требования вакансии...")

    requirements = extract_vacancy_requirements(
        vacancy_title,
        vacancy_description,
        language,
    )

    matched_requirements = []
    unsupported_requirements = []
    ignored_soft_requirements = []

    for index, requirement in enumerate(
        requirements,
        start=1,
    ):
        print(
            f"[TAILOR] Requirement {index}/{len(requirements)} "
            f"[{requirement.kind}]: "
            f"{requirement.text[:90]}"
        )

        if is_soft_requirement(
            requirement.text
        ):
            print("    -> SOFT / IGNORED")
            ignored_soft_requirements.append(
                requirement.text
            )
            continue

        matched, _ = classify_requirement(
            requirement,
            evidence,
        )

        if matched:
            print("    -> MATCHED")
            matched_requirements.append(
                requirement.text
            )
        else:
            print("    -> UNSUPPORTED")
            unsupported_requirements.append(
                requirement.text
            )

    key_skills = select_key_skills(
        vacancy_title,
        vacancy_description,
        allowed_skills,
        language,
    )

    # Diagnostic notes only; no imperative language.
    tailoring_notes = []

    if matched_requirements:
        tailoring_notes.append(
            "Акцент сделан на подтвержденных требованиях вакансии: "
            + "; ".join(
                matched_requirements[:3]
            )
            + "."
        )

    if unsupported_requirements:
        tailoring_notes.append(
            "Не заявлялся неподтвержденный опыт: "
            + "; ".join(
                unsupported_requirements[:3]
            )
            + "."
        )

    if ignored_soft_requirements:
        tailoring_notes.append(
            "Soft skills не использовались как factual match/gap: "
            + "; ".join(
                ignored_soft_requirements[:3]
            )
            + "."
        )

    tailoring_notes.append(
        "Опыт и достижения сохранены в пределах исходных фактов master_resume.yaml."
    )

    return MetaResult(
        key_skills=key_skills,
        matched_requirements=matched_requirements,
        unsupported_requirements=unsupported_requirements,
        tailoring_notes=tailoring_notes,
    )


# ============================================================
# GENERATE COMPLETE RESUME
# ============================================================

def generate_tailored_resume(
    vacancy_title: str,
    vacancy_description: str,
    decision: ResumeMatchDecision,
) -> TailoredResume:
    master = load_master_resume()

    language = detect_vacancy_language(
        vacancy_title,
        vacancy_description,
    )

    target_title = localize_target_title(
        get_safe_target_title(
            decision,
            master,
        ),
        language,
    )

    experience_ids = choose_experience_ids(
        decision,
        master,
    )

    experience_map = get_experience_map(master)

    print("[TAILOR] Генерирую summary...")

    summary, summary_fact_ids = generate_summary(
        vacancy_title=vacancy_title,
        vacancy_description=vacancy_description,
        decision=decision,
        master=master,
        language=language,
    )

    generated_experiences = []

    for index, experience_id in enumerate(
        experience_ids,
        start=1,
    ):
        experience = experience_map[experience_id]

        print(
            f"[TAILOR] Опыт {index}/{len(experience_ids)}: "
            f"{experience['company']} / "
            f"{experience['position']}"
        )

        generated_experiences.append(
            generate_experience_block(
                experience=experience,
                vacancy_title=vacancy_title,
                vacancy_description=vacancy_description,
                language=language,
            )
        )

    print("[TAILOR] Формирую skills и requirements...")

    meta = generate_meta(
        vacancy_title=vacancy_title,
        vacancy_description=vacancy_description,
        master=master,
        experience_ids=experience_ids,
        language=language,
    )

    resume = TailoredResume(
        target_title=target_title,
        summary=summary,
        summary_fact_ids=summary_fact_ids,
        key_skills=meta.key_skills,
        experience=generated_experiences,
        matched_requirements=meta.matched_requirements,
        unsupported_requirements=meta.unsupported_requirements,
        tailoring_notes=meta.tailoring_notes,
    )

    print("[TAILOR] Local validation PASSED.")

    return resume


# ============================================================
# DETERMINISTIC FACT-CHECK HELPERS
# ============================================================

def audit_summary(
    resume: TailoredResume,
    master: dict,
) -> tuple[list[str], list[str]]:
    """
    Deterministic summary audit.

    Summary is generated from fixed templates and whitelisted global fact IDs,
    so we only verify that all referenced IDs exist.
    """
    all_facts = get_all_fact_map(master)

    missing = [
        fact_id
        for fact_id in resume.summary_fact_ids
        if fact_id not in all_facts
    ]

    if missing:
        return (
            [
                "SUMMARY содержит неизвестные source fact ids: "
                + ", ".join(missing)
            ],
            [],
        )

    return [], []


def audit_experience_block(
    generated: GeneratedExperience,
    source_experience: dict,
) -> tuple[list[str], list[str]]:
    """
    Deterministic check.

    Experience bullets are inserted verbatim from master_resume.yaml,
    so no LLM audit is necessary here.
    """
    fact_map = {
        fact["id"]: fact["text"]
        for fact in source_experience.get("facts", [])
    }

    unsupported = []

    for bullet in generated.bullets:
        if len(bullet.source_fact_ids) != 1:
            unsupported.append(
                f"{source_experience['company']}: "
                f"bullet должен иметь ровно один source_fact_id"
            )
            continue

        fact_id = bullet.source_fact_ids[0]
        source_text = fact_map.get(fact_id)

        if source_text is None:
            unsupported.append(
                f"{source_experience['company']}: "
                f"неизвестный source_fact_id {fact_id}"
            )
            continue

        if normalize_text(bullet.text) != normalize_text(source_text):
            unsupported.append(
                f"{source_experience['company']}: "
                f"bullet отличается от master fact: {bullet.text}"
            )

    return unsupported, []


# ============================================================
# FACT CHECK
# ============================================================

def llm_fact_check(
    resume: TailoredResume,
    vacancy_title: str,
    vacancy_description: str,
) -> FactCheckResult:
    """
    Important:
    We no longer ask Gemma to produce one huge JSON object
    containing long explanation strings.

    Instead:
    - 1 tiny boolean audit for summary;
    - 1 tiny boolean-array audit per employer;
    - Python itself builds FactCheckResult.

    This removes the malformed-JSON failure we were hitting.
    """
    master = load_master_resume()
    experience_map = get_experience_map(master)

    unsupported_claims = []
    suspicious_claims = []
    notes = []

    print("[FACT CHECK] Summary...")

    summary_unsupported, summary_suspicious = audit_summary(
        resume,
        master,
    )

    unsupported_claims.extend(summary_unsupported)
    suspicious_claims.extend(summary_suspicious)

    for index, generated in enumerate(
        resume.experience,
        start=1,
    ):
        source_experience = experience_map[
            generated.experience_id
        ]

        print(
            f"[FACT CHECK] Опыт {index}/{len(resume.experience)}: "
            f"{source_experience['company']} (deterministic)"
        )

        block_unsupported, block_suspicious = audit_experience_block(
            generated,
            source_experience,
        )

        unsupported_claims.extend(block_unsupported)
        suspicious_claims.extend(block_suspicious)

    # Local checks for meta blocks:
    # unsupported_requirements are allowed to be empty.
    # We deliberately do not let them decide factual validity
    # because the resume itself is the safety-critical part.
    if resume.unsupported_requirements:
        notes.append(
            "Unsupported requirements сформированы по вакансии; "
            "они не входят в factual-validity резюме."
        )

    valid = len(unsupported_claims) == 0

    if valid:
        print("[FACT CHECK] PASSED.")
    else:
        print(
            f"[FACT CHECK] FAILED: "
            f"{len(unsupported_claims)} unsupported claim(s)."
        )

    return FactCheckResult(
        valid=valid,
        unsupported_claims=unsupported_claims,
        suspicious_claims=suspicious_claims,
        notes=notes,
    )


# ============================================================
# RENDER
# ============================================================

def render_resume_text(
    resume: TailoredResume,
) -> str:
    master = load_master_resume()
    experience_map = get_experience_map(master)

    lines = []

    lines.append(resume.target_title)
    lines.append("=" * len(resume.target_title))
    lines.append("")

    lines.append("ОБО МНЕ")
    lines.append("")
    lines.append(resume.summary)
    lines.append("")

    lines.append("КЛЮЧЕВЫЕ НАВЫКИ")
    lines.append("")

    for skill in resume.key_skills:
        lines.append(f"- {skill}")

    lines.append("")
    lines.append("ОПЫТ")
    lines.append("")

    for generated in resume.experience:
        source = experience_map[
            generated.experience_id
        ]

        lines.append(source["company"])
        lines.append(source["position"])
        lines.append(source["period"])

        for bullet in generated.bullets:
            lines.append(f"- {bullet.text}")

        lines.append("")

    lines.append("MATCHED REQUIREMENTS")
    lines.append("")

    if resume.matched_requirements:
        for item in resume.matched_requirements:
            lines.append(f"+ {item}")
    else:
        lines.append("Нет.")

    lines.append("")
    lines.append("UNSUPPORTED REQUIREMENTS")
    lines.append("")

    if resume.unsupported_requirements:
        for item in resume.unsupported_requirements:
            lines.append(f"- {item}")
    else:
        lines.append("Нет выявленных.")

    lines.append("")
    lines.append("TAILORING NOTES")
    lines.append("")

    if resume.tailoring_notes:
        for item in resume.tailoring_notes:
            lines.append(f"- {item}")
    else:
        lines.append("Нет.")

    return "\n".join(lines)


# ============================================================
# SAVE
# ============================================================

def save_resume_preview(
    resume: TailoredResume,
    vacancy_id: int | None = None,
) -> Path:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    prefix = (
        f"{vacancy_id}_"
        if vacancy_id is not None
        else ""
    )

    filename = (
        prefix
        + make_safe_filename(resume.target_title)
        + ".txt"
    )

    output_path = OUTPUT_DIR / filename

    output_path.write_text(
        render_resume_text(resume),
        encoding="utf-8",
    )

    return output_path
