from __future__ import annotations

import json
import os
import re
from typing import Any

from app.llm import LLMProvider
from app.models import VacancyEvaluation


MAX_LLM_ATTEMPTS = 3

# Final score is deterministic. The LLM scores dimensions only.
ROLE_WEIGHT = 0.35
SENIORITY_WEIGHT = 0.20
DOMAIN_WEIGHT = 0.15
RESPONSIBILITY_WEIGHT = 0.30

DEFAULT_APPLY_THRESHOLD = int(
    os.getenv("SCORE_THRESHOLD", "80")
)
DEFAULT_REVIEW_THRESHOLD = int(
    os.getenv("HH_REVIEW_THRESHOLD", "70")
)

SIGNATURE_RU = "С уважением,\nАлександр Руденко"
SIGNATURE_EN = "Best regards,\nAleksandr Rudenko"

PLACEHOLDER_MARKERS = [
    "[ваше имя]",
    "[имя]",
    "<ваше имя>",
    "<имя>",
    "{ваше имя}",
    "{имя}",
    "{name}",
    "[name]",
    "<name>",
    "your name",
]

BAD_COVER_PHRASES_RU = [
    "уважаемый hr",
    "уважаемый рекрутер",
    "уверен, что",
    "уверен, мой",
    "готов обсудить",
]

BAD_COVER_PHRASES_EN = [
    "dear hr",
    "dear recruiter",
    "i am confident that",
    "i'm confident that",
    "happy to discuss",
]


# ---------------------------------------------------------------------------
# Deterministic evidence guard
# ---------------------------------------------------------------------------
# These are NOT inferred skills. They are explicit facts from the canonical
# candidate profile. The guard prevents the LLM from marking a confirmed
# competency as "missing" merely because the vacancy uses different wording.
#
# Important:
# - The guard DOES NOT invent new experience.
# - It DOES NOT force a vacancy to be accepted.
# - It DOES NOT change role/domain fit by itself.
# - It only removes false-negative claims from missing/gaps/red_flags and
#   slightly reinforces responsibility/seniority scores when the vacancy
#   explicitly asks for a competency that is directly confirmed in the resume.

CONFIRMED_COMPETENCY_GUARDS = [
    {
        "key": "program_management",
        "vacancy_markers": [
            "program manager",
            "programme manager",
            "program management",
            "programme management",
            "управление программ",
            "руководитель программ",
            "руководитель программы",
            "программой проектов",
        ],
        "resume_markers": [
            "program management",
            "управление портфелем проектов",
            "портфель 30+",
            "pmo",
        ],
        "negative_markers": [
            "program management",
            "programme management",
            "управление программ",
            "опыт управления программ",
            "программами проектов",
        ],
        "strength_ru": (
            "Подтверждён опыт Program Management / управления "
            "портфелем и несколькими параллельными IT-проектами."
        ),
        "strength_en": (
            "Confirmed Program Management experience across a portfolio "
            "of multiple parallel IT projects."
        ),
        "seniority_floor": 80,
        "responsibility_floor": 80,
    },
    {
        "key": "portfolio_pmo",
        "vacancy_markers": [
            "portfolio",
            "портфел",
            "pmo",
            "project office",
            "проектного офиса",
            "проектный офис",
            "project governance",
        ],
        "resume_markers": [
            "портфель 30+",
            "управление портфелем",
            "pmo",
            "проектного управления bss",
        ],
        "negative_markers": [
            "portfolio",
            "портфел",
            "pmo",
            "project office",
            "проектного офиса",
            "project governance",
        ],
        "strength_ru": (
            "Подтверждён опыт PMO и управления портфелем 30+ IT-проектов."
        ),
        "strength_en": (
            "Confirmed PMO and portfolio management experience across "
            "30+ IT projects."
        ),
        "seniority_floor": 80,
        "responsibility_floor": 82,
    },
    {
        "key": "project_manager_leadership",
        "vacancy_markers": [
            "руководство менеджерами проектов",
            "управление менеджерами проектов",
            "руководство project manager",
            "управление project manager",
            "team of project managers",
            "project managers team",
            "manage project managers",
            "lead project managers",
            "head of projects",
            "head of project management",
        ],
        "resume_markers": [
            "управление командой руководителей проектов",
            "менеджеры проектов",
            "команда руководителей проектов",
        ],
        "negative_markers": [
            "руководств",
            "управление менеджерами проектов",
            "управление руководителями проектов",
            "project managers",
            "team of pm",
            "people management",
        ],
        "strength_ru": (
            "Подтверждён опыт управления командой руководителей проектов."
        ),
        "strength_en": (
            "Confirmed experience leading a team of project managers."
        ),
        "seniority_floor": 85,
        "responsibility_floor": 82,
    },
    {
        "key": "people_management",
        "vacancy_markers": [
            "people management",
            "team management",
            "team leadership",
            "управление командой",
            "руководство командой",
            "управление сотрудниками",
            "линейное управление",
            "матричное управление",
        ],
        "resume_markers": [
            "до 70 человек",
            "около 70 сотрудников",
            "найм более 40",
            "найм 40+",
            "управление крупным подразделением",
        ],
        "negative_markers": [
            "people management",
            "team management",
            "team leadership",
            "управление командой",
            "руководство командой",
            "управление сотрудниками",
            "линейное управление",
            "матричное управление",
        ],
        "strength_ru": (
            "Подтверждён people management: команды до 70 человек, "
            "найм 40+ специалистов."
        ),
        "strength_en": (
            "Confirmed people-management experience: teams up to 70 people "
            "and 40+ hires."
        ),
        "seniority_floor": 82,
        "responsibility_floor": 78,
    },
    {
        "key": "agile",
        "vacancy_markers": [
            "agile",
            "scrum",
            "kanban",
            "less",
            "waterfall",
            "гибридн",
            "hybrid",
        ],
        "resume_markers": [
            "agile",
            "scrum",
            "kanban",
            "less",
            "waterfall",
        ],
        "negative_markers": [
            "agile",
            "scrum",
            "kanban",
            "less",
            "waterfall",
            "гибрид",
            "hybrid",
        ],
        "strength_ru": (
            "Подтверждён практический опыт Agile, Scrum/LeSS, Kanban, "
            "Waterfall и гибридного delivery."
        ),
        "strength_en": (
            "Confirmed hands-on delivery experience with Agile, Scrum/LeSS, "
            "Kanban, Waterfall and hybrid models."
        ),
        "seniority_floor": None,
        "responsibility_floor": 78,
    },
    {
        "key": "budget_resources",
        "vacancy_markers": [
            "budget",
            "бюджет",
            "resource management",
            "управление ресурс",
            "capacity management",
            "capacity planning",
        ],
        "resume_markers": [
            "350 млн",
            "управление бюджетом",
            "бюджетирование",
            "управление ресурсами",
            "до 70 человек",
        ],
        "negative_markers": [
            "budget",
            "бюджет",
            "resource management",
            "управление ресурс",
            "capacity",
        ],
        "strength_ru": (
            "Подтверждено управление бюджетом около 350 млн ₽ и ресурсами "
            "крупных IT-команд."
        ),
        "strength_en": (
            "Confirmed budget ownership of about RUB 350M and resource "
            "management for large IT teams."
        ),
        "seniority_floor": 78,
        "responsibility_floor": 78,
    },
    {
        "key": "stakeholders",
        "vacancy_markers": [
            "stakeholder",
            "c-level",
            "c level",
            "ceo-1",
            "executive",
            "бизнес-заказ",
            "стейкхолдер",
        ],
        "resume_markers": [
            "c-level",
            "ceo-1",
            "бизнес-заказчиками",
            "stakeholder management",
        ],
        "negative_markers": [
            "stakeholder",
            "c-level",
            "c level",
            "ceo-1",
            "executive",
            "бизнес-заказ",
            "стейкхолдер",
        ],
        "strength_ru": (
            "Подтверждён опыт работы с C-level, CEO-1 и бизнес-заказчиками."
        ),
        "strength_en": (
            "Confirmed experience working with C-level, CEO-1 and business "
            "stakeholders."
        ),
        "seniority_floor": 80,
        "responsibility_floor": 78,
    },
    {
        "key": "delivery_project_management",
        "vacancy_markers": [
            "delivery",
            "end-to-end",
            "full cycle",
            "полного цикла",
            "project management",
            "управление проект",
            "roadmap",
            "risk management",
            "управление рисками",
            "change management",
            "управление изменениями",
        ],
        "resume_markers": [
            "управление it-проектами полного цикла",
            "delivery management",
            "управление roadmap",
            "управление рисками",
            "управление изменениями",
        ],
        "negative_markers": [
            "delivery",
            "project management",
            "управление проект",
            "roadmap",
            "risk management",
            "управление рисками",
            "change management",
            "управление изменениями",
        ],
        "strength_ru": (
            "Подтверждён end-to-end Project/Delivery Management: roadmap, "
            "требования, риски, изменения, SLA и эксплуатация."
        ),
        "strength_en": (
            "Confirmed end-to-end Project/Delivery Management across roadmap, "
            "requirements, risks, change, SLA and operations."
        ),
        "seniority_floor": 78,
        "responsibility_floor": 80,
    },
]


def _normalize_evidence_text(
    text: str,
) -> str:
    value = (
        text
        or ""
    ).lower()
    value = value.replace(
        "ё",
        "е",
    )
    value = re.sub(
        r"\s+",
        " ",
        value,
    )
    return value.strip()


def _contains_any_marker(
    text: str,
    markers: list[str],
) -> bool:
    normalized = _normalize_evidence_text(
        text
    )

    return any(
        _normalize_evidence_text(marker)
        in normalized
        for marker in markers
    )


def _item_hits_guard(
    item: str,
    markers: list[str],
) -> bool:
    normalized = _normalize_evidence_text(
        item
    )

    return any(
        _normalize_evidence_text(marker)
        in normalized
        for marker in markers
    )


def _dedupe_text_items(
    items: list[str] | None,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for item in items or []:
        value = str(
            item
        ).strip()

        if not value:
            continue

        key = _normalize_evidence_text(
            value
        )

        if key in seen:
            continue

        seen.add(
            key
        )
        result.append(
            value
        )

    return result


def _apply_confirmed_evidence_guard(
    result: VacancyEvaluation,
    *,
    resume: str,
    vacancy: str,
    language: str,
) -> VacancyEvaluation:
    """
    Remove false negative claims for competencies explicitly present
    in the candidate profile.

    The guard only activates when BOTH conditions are true:
    1) the vacancy mentions the competency;
    2) the supplied resume/profile contains explicit evidence.

    Therefore it cannot manufacture a skill that is absent from the profile.
    """
    active_guards: list[dict] = []

    for guard in CONFIRMED_COMPETENCY_GUARDS:
        vacancy_relevant = _contains_any_marker(
            vacancy,
            guard["vacancy_markers"],
        )
        resume_confirms = _contains_any_marker(
            resume,
            guard["resume_markers"],
        )

        if (
            vacancy_relevant
            and resume_confirms
        ):
            active_guards.append(
                guard
            )

    if not active_guards:
        return result

    protected_markers: list[str] = []

    for guard in active_guards:
        protected_markers.extend(
            guard["negative_markers"]
        )

    def clean_false_negatives(
        items: list[str] | None,
    ) -> list[str]:
        cleaned: list[str] = []

        for item in items or []:
            value = str(
                item
            ).strip()

            if not value:
                continue

            if _item_hits_guard(
                value,
                protected_markers,
            ):
                print(
                    "[EVIDENCE GUARD] removed false negative: "
                    f"{value}"
                )
                continue

            cleaned.append(
                value
            )

        return _dedupe_text_items(
            cleaned
        )

    result.must_have_missing = clean_false_negatives(
        result.must_have_missing
    )
    result.nice_to_have_missing = clean_false_negatives(
        result.nice_to_have_missing
    )
    result.gaps = clean_false_negatives(
        result.gaps
    )
    result.red_flags = clean_false_negatives(
        result.red_flags
    )

    strengths = _dedupe_text_items(
        result.strengths
    )

    normalized_strengths = _normalize_evidence_text(
        " ".join(
            strengths
        )
    )

    for guard in active_guards:
        strength = (
            guard["strength_ru"]
            if language == "ru"
            else guard["strength_en"]
        )

        if not _item_hits_guard(
            normalized_strengths,
            guard["negative_markers"],
        ):
            strengths.append(
                strength
            )
            normalized_strengths = (
                _normalize_evidence_text(
                    normalized_strengths
                    + " "
                    + strength
                )
            )

        seniority_floor = guard.get(
            "seniority_floor"
        )
        responsibility_floor = guard.get(
            "responsibility_floor"
        )

        if seniority_floor is not None:
            result.seniority_match = max(
                _clamp_score(
                    result.seniority_match
                ),
                int(
                    seniority_floor
                ),
            )

        if responsibility_floor is not None:
            result.responsibility_match = max(
                _clamp_score(
                    result.responsibility_match
                ),
                int(
                    responsibility_floor
                ),
            )

    result.strengths = _dedupe_text_items(
        strengths
    )

    return result


def _extract_response_text(response: Any) -> str:
    if hasattr(response, "message"):
        message = response.message
        if hasattr(message, "content"):
            return message.content or ""

    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""

    raise RuntimeError(
        "Не удалось извлечь текст из ответа Ollama."
    )


def _extract_json(text: str) -> dict:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"```$",
            "",
            text,
        ).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "LLM не вернула JSON."
        )

    return json.loads(
        text[start : end + 1]
    )


def _clamp_score(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0

    return max(0, min(100, number))


def _weighted_score(
    role_match: int,
    seniority_match: int,
    domain_match: int,
    responsibility_match: int,
) -> int:
    score = (
        role_match * ROLE_WEIGHT
        + seniority_match * SENIORITY_WEIGHT
        + domain_match * DOMAIN_WEIGHT
        + responsibility_match * RESPONSIBILITY_WEIGHT
    )

    return _clamp_score(score)


def _detect_language(vacancy: str) -> str:
    cyrillic = len(
        re.findall(
            r"[А-Яа-яЁё]",
            vacancy or "",
        )
    )
    latin = len(
        re.findall(
            r"[A-Za-z]",
            vacancy or "",
        )
    )

    return (
        "ru"
        if cyrillic >= latin
        else "en"
    )


def _strip_existing_signature(
    text: str,
) -> str:
    result = (
        text
        or ""
    ).strip()

    # RU signature + anything after it.
    result = re.sub(
        r"(?is)\n*\s*с\s+уважением\s*,?\s*.*$",
        "",
        result,
    ).strip()

    # EN signature + anything after it.
    result = re.sub(
        r"(?is)\n*\s*best\s+regards\s*,?\s*.*$",
        "",
        result,
    ).strip()

    # Bare candidate name at the end.
    result = re.sub(
        r"(?is)\n*\s*(?:александр\s+руденко|aleksandr\s+rudenko)\s*$",
        "",
        result,
    ).strip()

    return result


def _contains_placeholder(
    text: str,
) -> bool:
    lower = (
        text
        or ""
    ).lower()

    return any(
        marker in lower
        for marker in PLACEHOLDER_MARKERS
    )


def _contains_bad_phrase(
    text: str,
    language: str,
) -> bool:
    lower = (
        text
        or ""
    ).lower()

    phrases = (
        BAD_COVER_PHRASES_RU
        if language == "ru"
        else BAD_COVER_PHRASES_EN
    )

    return any(
        phrase in lower
        for phrase in phrases
    )


def _contains_third_person_cover(
    text: str,
    language: str,
) -> bool:
    lower = (
        text
        or ""
    ).lower()

    if language == "ru":
        patterns = [
            r"\bкандидат\b",
            r"\bкандидату\b",
            r"\bкандидата\b",
            r"\bего опыт\b",
            r"\bего знания\b",
            r"\bего навыки\b",
            r"\bего компетенц",
            r"\bему позволит\b",
            r"\bему помогут\b",
            r"\bон обладает\b",
            r"\bон имеет\b",
            r"\bон умеет\b",
            r"\bон сможет\b",
        ]
    else:
        patterns = [
            r"\bthe candidate\b",
            r"\bhis experience\b",
            r"\bhis skills\b",
            r"\bhis knowledge\b",
            r"\bhe has\b",
            r"\bhe is\b",
            r"\bhe can\b",
        ]

    return any(
        re.search(
            pattern,
            lower,
            flags=re.IGNORECASE,
        )
        for pattern in patterns
    )



def _normalize_cover_letter(
    text: str,
    language: str,
) -> str:
    """
    LLM writes only the body. Python owns the signature.
    """
    body = _strip_existing_signature(
        text
    )

    if not body:
        return ""

    if _contains_placeholder(body):
        return ""

    if _contains_bad_phrase(
        body,
        language,
    ):
        return ""

    if _contains_third_person_cover(
        body,
        language,
    ):
        return ""

    # Fix the common malformed greeting:
    # "Здравствуйте, Имею..." -> "Здравствуйте!\n\nИмею..."
    if language == "ru":
        body = re.sub(
            r"^\s*Здравствуйте\s*,\s*",
            "Здравствуйте!\n\n",
            body,
            flags=re.IGNORECASE,
        )
        signature = SIGNATURE_RU
    else:
        body = re.sub(
            r"^\s*Hello\s*,\s*",
            "Hello!\n\n",
            body,
            flags=re.IGNORECASE,
        )
        signature = SIGNATURE_EN

    body = body.strip()

    if len(body) < 80:
        return ""

    return (
        body
        + "\n\n"
        + signature
    )


def _fallback_cover_letter(
    result: VacancyEvaluation,
    language: str,
) -> str:
    """
    Deterministic fallback assembled only from already validated
    evaluation fields. No invented employer-specific claims.
    """
    strengths = [
        str(item).strip()
        for item in (result.strengths or [])
        if str(item).strip()
    ][:3]

    if language == "ru":
        parts = [
            "Здравствуйте!",
            "",
            "Рассматриваю эту позицию как релевантную моему опыту.",
        ]

        if strengths:
            parts.append(
                "Из наиболее релевантного: "
                + "; ".join(strengths)
                + "."
            )

        parts.extend(
            [
                "Буду рад применить этот опыт в задачах команды.",
                "",
                SIGNATURE_RU,
            ]
        )
    else:
        parts = [
            "Hello!",
            "",
            "I see this position as relevant to my experience.",
        ]

        if strengths:
            parts.append(
                "The most relevant strengths are: "
                + "; ".join(strengths)
                + "."
            )

        parts.extend(
            [
                "I would be glad to apply this experience to the team's work.",
                "",
                SIGNATURE_EN,
            ]
        )

    return "\n".join(parts).strip()


class VacancyEvaluator:
    def __init__(
        self,
        llm: LLMProvider | None = None,
    ):
        self.llm = (
            llm
            if llm is not None
            else LLMProvider()
        )

    def _ask(
        self,
        prompt: str,
    ) -> VacancyEvaluation:
        schema = VacancyEvaluation.model_json_schema()
        last_error: Exception | None = None

        for attempt in range(
            1,
            MAX_LLM_ATTEMPTS + 1,
        ):
            try:
                response = self.llm.chat(
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    format_schema=schema,
                )

                raw = _extract_response_text(
                    response
                ).strip()

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = _extract_json(raw)

                return VacancyEvaluation.model_validate(
                    data
                )

            except Exception as exc:
                last_error = exc

                print(
                    "[EVALUATOR] "
                    f"attempt {attempt}/"
                    f"{MAX_LLM_ATTEMPTS} failed: "
                    f"{exc}"
                )

        raise RuntimeError(
            "VacancyEvaluator не получил "
            "корректный structured response "
            f"после {MAX_LLM_ATTEMPTS} попыток."
        ) from last_error

    def evaluate(
        self,
        resume: str,
        vacancy: str,
        preferences: dict | None = None,
    ) -> VacancyEvaluation:
        preferences = (
            preferences
            if isinstance(preferences, dict)
            else {}
        )

        prompt = f"""
Ты оцениваешь вакансию для кандидата.

Твоя задача:
1. Сопоставить вакансию с резюме и предпочтениями кандидата.
2. Не придумывать опыт, технологии, достижения или знания кандидата.
3. Вернуть структурированную оценку.
4. Написать сопроводительное письмо ТОЛЬКО на основе фактов из резюме.

ВАЖНЫЕ ПРАВИЛА ОЦЕНКИ

- role_match:
  насколько сама роль соответствует профессиональному профилю кандидата.

- seniority_match:
  насколько уровень роли соответствует опыту и масштабу ответственности кандидата.

- domain_match:
  насколько релевантен предметный/отраслевой контекст.
  Не занижай сильно оценку только из-за другой отрасли,
  если основные управленческие задачи переносимы.

- responsibility_match:
  насколько совпадают реальные задачи и зоны ответственности.

- must_have_missing:
  только действительно обязательные требования вакансии,
  которых НЕТ в резюме.
  Не выдумывай отсутствие навыка, если вакансия его не требует.
  ПЕРЕД тем как поместить навык сюда, проверь весь профиль кандидата
  и смысловые эквиваленты. Разные формулировки одного и того же опыта
  НЕ являются отсутствием опыта.

- nice_to_have_missing:
  только необязательные требования, явно указанные вакансией
  и не подтверждённые резюме.

- strengths:
  только конкретные подтверждённые резюме сильные стороны,
  релевантные этой вакансии.

- gaps:
  реальные расхождения между вакансией и резюме.
  Не записывай в gap личностные качества, которые невозможно
  достоверно подтвердить по CV.
  ЗАПРЕЩЕНО писать в gaps отсутствие Program Management,
  Portfolio Management, PMO, управления Project Managers,
  people management, Agile/Scrum/LeSS/Kanban/Waterfall,
  budget/resource management, C-level stakeholder management,
  если соответствующий факт присутствует в профиле кандидата.

- red_flags:
  только существенные причины не откликаться:
  явный mismatch роли, seniority, критический must-have,
  неподходящие условия из предпочтений и т.п.

КРИТИЧЕСКОЕ ПРАВИЛО СМЫСЛОВОЙ ЭКВИВАЛЕНТНОСТИ

Если вакансия использует термин, отличающийся от формулировки в профиле,
не считай компетенцию отсутствующей, если фактический опыт эквивалентен.

Примеры:
- team of Project Managers / управление PM -> управление командой руководителей проектов;
- Program Management -> PMO + портфель 30+ проектов + несколько параллельных инициатив;
- Portfolio Management -> управление портфелем 30+ проектов;
- people management -> команды до 70 человек + найм 40+ + управление PM;
- Agile delivery -> Agile + Scrum/LeSS + Kanban;
- hybrid delivery -> Agile + Waterfall;
- resource/capacity management -> управление ресурсами крупных IT-команд;
- executive stakeholders -> C-level + CEO-1 + бизнес-заказчики;
- delivery ownership -> Delivery Management + roadmap + требования + SLA + эксплуатация.

Отсутствие буквального совпадения слов НЕ является доказательством отсутствия опыта.

ПРАВИЛА СОПРОВОДИТЕЛЬНОГО ПИСЬМА

- cover_letter нужен для вакансий, которые выглядят разумными для отклика.
- Для явного reject cover_letter должен быть пустой строкой.
- Письмо должно быть коротким: примерно 700-1200 знаков максимум.
- Начало для русского: "Здравствуйте!"
- Не пиши "Уважаемый HR", "Уважаемый рекрутер".
- Не пиши "Уверен, что...", "Уверен, мой...".
- Не пиши "Готов обсудить...".
- Не используй placeholders:
  [Ваше имя], [Имя], <имя>, {{name}}, Your Name и подобные.
- НЕ добавляй подпись и имя кандидата вообще.
  Python добавит подпись сам.
- Не пересказывай всё резюме.
- Выбери 2-3 наиболее релевантных факта/результата.
- Не утверждай причинно-следственные связи, которых нет в резюме.
- Не называй технологию/домен опытом кандидата,
  если этого нет в резюме.
- Не пиши, что кандидат умеет конкретную вещь,
  если это нельзя подтвердить резюме.
- Пиши сопроводительное СТРОГО ОТ ПЕРВОГО ЛИЦА, как будто кандидат сам отправляет письмо работодателю.
- Используй формулировки "мой опыт", "я руководил", "я отвечал", "в моём опыте", а не описание кандидата со стороны.
- ЗАПРЕЩЕНО писать о кандидате в третьем лице: "кандидат обладает", "его опыт", "он имеет", "ему позволит" и подобное.
- Стиль: деловой, живой, без HR-воды и самовосхваления.

РЕЗЮМЕ КАНДИДАТА

{resume[:32000]}

ПРЕДПОЧТЕНИЯ КАНДИДАТА

{json.dumps(
    preferences,
    ensure_ascii=False,
    indent=2,
)[:8000]}

ВАКАНСИЯ

{vacancy[:20000]}

Верни результат строго по схеме VacancyEvaluation.
Поле score можешь оценить предварительно — Python затем
пересчитает его детерминированно из четырёх match-полей.
""".strip()

        result = self._ask(
            prompt
        )

        # Normalize all model scores.
        result.role_match = _clamp_score(
            result.role_match
        )
        result.seniority_match = _clamp_score(
            result.seniority_match
        )
        result.domain_match = _clamp_score(
            result.domain_match
        )
        result.responsibility_match = _clamp_score(
            result.responsibility_match
        )

        language = _detect_language(
            vacancy
        )

        result = _apply_confirmed_evidence_guard(
            result,
            resume=resume,
            vacancy=vacancy,
            language=language,
        )

        result.score = _weighted_score(
            role_match=result.role_match,
            seniority_match=result.seniority_match,
            domain_match=result.domain_match,
            responsibility_match=(
                result.responsibility_match
            ),
        )

        # Decision is deterministic as well.
        apply_threshold = DEFAULT_APPLY_THRESHOLD
        review_threshold = min(
            DEFAULT_REVIEW_THRESHOLD,
            apply_threshold,
        )

        has_red_flags = bool(
            result.red_flags
        )

        if (
            result.score >= apply_threshold
            and not has_red_flags
        ):
            result.decision = "apply"

        elif (
            result.score >= review_threshold
            and not has_red_flags
        ):
            result.decision = "review"

        else:
            result.decision = "reject"

        if result.decision == "reject":
            result.cover_letter = ""
            return result

        normalized = _normalize_cover_letter(
            result.cover_letter,
            language,
        )

        if not normalized:
            normalized = _fallback_cover_letter(
                result,
                language,
            )

        # Last deterministic safety check.
        if (
            _contains_placeholder(normalized)
            or _contains_bad_phrase(
                normalized,
                language,
            )
            or _contains_third_person_cover(
                normalized,
                language,
            )
        ):
            normalized = _fallback_cover_letter(
                result,
                language,
            )

        result.cover_letter = normalized

        return result
