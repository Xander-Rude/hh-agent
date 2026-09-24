from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm import LLMProvider


PROMPT_VERSION = "clean-shadow-prompt-v11"
SCORING_VERSION = "clean-shadow-score-v3"
GATE_VERSION = "clean-shadow-gates-v7"
ROUTING_VERSION = "clean-shadow-routing-v1"
COMPANY_POLICY_VERSION = "clean-shadow-company-v1"


RoleFamily = Literal[
    "PROJECT_CORE",
    "PROJECT_DELIVERY",
    "TECHNICAL_PROJECT",
    "IMPLEMENTATION_TRANSFORMATION",
    "BUSINESS_IT_DELIVERY",
    "PROGRAM_DELIVERY",
    "PMO_PORTFOLIO_GOVERNANCE",
    "PRODUCT",
    "ENGINEERING_MANAGEMENT",
    "IT_FUNCTION_LEADERSHIP",
    "SERVICE_OPERATIONS",
    "DATA_AI_FUNCTION",
    "SALES_ACCOUNT_BD",
    "EXECUTIVE_OPERATIONS",
    "BUSINESS_ANALYSIS",
    "BUSINESS_FUNCTION",
    "NON_IT_PROJECT",
    "OTHER_AMBIGUOUS",
]


CORE_ROLE_FAMILIES = {
    "PROJECT_CORE",
    "PROJECT_DELIVERY",
    "TECHNICAL_PROJECT",
}
ADJACENT_ROLE_FAMILIES = {
    "IMPLEMENTATION_TRANSFORMATION",
    "BUSINESS_IT_DELIVERY",
    "PROGRAM_DELIVERY",
}
NONCORE_ROLE_FAMILIES = {
    "PMO_PORTFOLIO_GOVERNANCE",
    "PRODUCT",
    "ENGINEERING_MANAGEMENT",
    "IT_FUNCTION_LEADERSHIP",
    "SERVICE_OPERATIONS",
    "DATA_AI_FUNCTION",
    "SALES_ACCOUNT_BD",
    "EXECUTIVE_OPERATIONS",
    "BUSINESS_ANALYSIS",
    "BUSINESS_FUNCTION",
    "NON_IT_PROJECT",
}


def effective_clean_role_class(
    extraction: "CleanShadowExtraction",
) -> str:
    """Resolve CLEAN eligibility from the primary object before model labels.

    The model can emit internally inconsistent structured fields. The primary
    object and lifecycle are closer to the actual job outcome than an enum
    label, so they win when they are decisive.
    """
    primary_object = extraction.primary_object
    lifecycle = extraction.project_lifecycle_ownership
    family = extraction.role_family_primary

    # These families are semantically non-core even when the extraction also
    # claims a full project lifecycle. Lifecycle depth must not convert the
    # subject of the job into PM delivery.
    if family in {
        "NON_IT_PROJECT",
        "BUSINESS_ANALYSIS",
        "BUSINESS_FUNCTION",
    }:
        return "noncore"

    if primary_object == "project":
        if lifecycle in {"full", "substantial"}:
            return "core"
        if lifecycle == "partial":
            return "adjacent"

    if primary_object == "program":
        if lifecycle in {"full", "substantial", "partial"}:
            return "adjacent"

    if primary_object in {
        "product",
        "portfolio",
        "engineering_function",
        "it_function",
        "service",
        "sales_account",
        "data_ai_function",
        "executive_support",
        "business_analysis",
        "business_function",
        "non_it_asset",
    }:
        return "noncore"

    if family in CORE_ROLE_FAMILIES:
        return "core"
    if family in ADJACENT_ROLE_FAMILIES:
        return "adjacent"
    if family in NONCORE_ROLE_FAMILIES:
        return "noncore"
    return "unknown"


class RequirementEvidence(BaseModel):
    name: str
    category: Literal[
        "language",
        "hands_on",
        "exact_stack",
        "exact_domain",
        "education_clearance",
        "work_auth",
        "other",
    ]
    criticality: Literal[
        "non_negotiable",
        "core",
        "preferred",
        "context",
    ]
    evidence_visibility: Literal[
        "CV_DIRECT",
        "CV_SEMANTIC",
        "COVER_SURFACED",
        "INTERNAL_ONLY",
        "UNCONFIRMED",
    ]
    match_quality: Literal["full", "partial", "none"]
    source_text: str = ""
    candidate_evidence: str = ""


class RequirementVisibilityReview(BaseModel):
    index: int = Field(ge=0)
    evidence_visibility: Literal[
        "CV_DIRECT",
        "CV_SEMANTIC",
        "COVER_SURFACED",
        "UNCONFIRMED",
    ]
    match_quality: Literal["full", "partial", "none"]
    candidate_evidence: str = ""


class RecruiterVisibilityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[RequirementVisibilityReview]
    role_narrative_coherence: Literal[
        "strong",
        "good",
        "strained",
        "incoherent",
    ]
    recent_relevant_evidence: Literal[
        "strong_recent",
        "recent_adjacent",
        "older_only",
        "none",
    ]
    seniority_autonomy_visibility: Literal[
        "strong",
        "good",
        "weak",
        "mismatch",
    ]
    domain_technical_visibility: Literal[
        "direct",
        "transferable",
        "weak",
        "none",
    ]
    visible_differentiators: Literal[
        "strong",
        "generic",
        "none",
    ]
    cover_surfaced_evidence: Literal[
        "effective",
        "neutral",
        "none",
        "oversell",
    ]
    top_invite_reasons: list[str] = Field(default_factory=list)
    invite_risks: list[str] = Field(default_factory=list)


class LearnedPatternReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant_pattern_keys: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class CleanShadowExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_family_primary: RoleFamily
    role_family_secondary: RoleFamily | None = None
    primary_object: Literal[
        "project",
        "program",
        "product",
        "portfolio",
        "engineering_function",
        "it_function",
        "service",
        "sales_account",
        "data_ai_function",
        "executive_support",
        "business_analysis",
        "business_function",
        "non_it_asset",
        "ambiguous",
    ]
    project_lifecycle_ownership: Literal[
        "full",
        "substantial",
        "partial",
        "none",
    ]
    clean_role_class: Literal[
        "core",
        "adjacent",
        "noncore",
        "unknown",
    ]
    role_confidence: float = Field(ge=0.0, le=1.0)
    role_rationale: str

    complexity_seniority: Literal[
        "strong",
        "good",
        "acceptable",
        "mismatch",
    ]
    technical_context_fit: Literal[
        "strong",
        "transferable",
        "generic",
        "weak",
    ]
    domain_affinity: Literal[
        "preferred",
        "direct",
        "transferable",
        "weak",
        "unwanted",
    ]
    change_outcome_fit: Literal[
        "strong",
        "substantial",
        "weak",
        "none",
    ]

    role_narrative_coherence: Literal[
        "strong",
        "good",
        "strained",
        "incoherent",
    ]
    recent_relevant_evidence: Literal[
        "strong_recent",
        "recent_adjacent",
        "older_only",
        "none",
    ]
    seniority_autonomy_visibility: Literal[
        "strong",
        "good",
        "weak",
        "mismatch",
    ]
    domain_technical_visibility: Literal[
        "direct",
        "transferable",
        "weak",
        "none",
    ]
    visible_differentiators: Literal[
        "strong",
        "generic",
        "none",
    ]
    cover_surfaced_evidence: Literal[
        "effective",
        "neutral",
        "none",
        "oversell",
    ]

    unwanted_domain_status: Literal["pass", "fail", "unknown"]
    location_work_auth_status: Literal["pass", "fail", "unknown"]
    requirements: list[RequirementEvidence]
    top_fit_reasons: list[str] = Field(default_factory=list)
    top_invite_reasons: list[str] = Field(default_factory=list)
    invite_risks: list[str] = Field(default_factory=list)
    learned_pattern_keys: list[str] = Field(default_factory=list)
    learned_positive_signals: list[str] = Field(default_factory=list)
    learned_risks: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ShadowScores:
    fit_score: int
    invite_score: int | None
    hard_stops: tuple[str, ...]
    routing_class: str
    route_reason_codes: tuple[str, ...]


def _response_text(response) -> str:
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if content:
            return str(content)
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
    raise RuntimeError("CLEAN shadow: empty LLM response")


def _extract_json(text: str) -> dict:
    body = (text or "").strip()
    body = re.sub(r"^\`\`\`(?:json)?", "", body, flags=re.I).strip()
    body = re.sub(r"\`\`\`$", "", body).strip()
    if not body.startswith("{"):
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            body = body[start : end + 1]
    return json.loads(body)


PROJECT_LIKE_FAMILIES = (
    CORE_ROLE_FAMILIES
    | ADJACENT_ROLE_FAMILIES
)

WORK_AUTH_SOURCE_RE = re.compile(
    r"(гражданств|разрешени.{0,12}работ|право.{0,12}работ|"
    r"work\s*authori[sz]ation|visa|виз[аы]|релокац|relocat|"
    r"место\s+работы|локаци)",
    re.I,
)
IT_FUNCTION_LEADERSHIP_PATTERNS = {
    "strategy": re.compile(
        r"(стратег\w*.{0,80}(?:IT|ИТ)[- ]?(?:направлен|функц|развит|ландшафт)|"
        r"(?:IT|ИТ)[- ]?(?:стратег|направлен).{0,80}(?:стратег|развит)|"
        r"\bIT\s+strategy\b|\btechnology\s+strategy\b)",
        re.I,
    ),
    "team": re.compile(
        r"(управлен\w*.{0,80}(?:сотрудник\w*.{0,30}(?:IT|ИТ)|"
        r"(?:IT|ИТ)[- ]?(?:команд|подраздел|направлен|функц))|"
        r"руковод\w*.{0,50}(?:IT|ИТ)[- ]?(?:команд|подраздел|направлен|функц)|"
        r"\b(?:manage|lead|head)\w*.{0,40}\bIT\s+(?:team|department|function)\b)",
        re.I,
    ),
    "operations": re.compile(
        r"(управлен\w*.{0,60}(?:IT|ИТ)[- ]?инфраструктур|"
        r"обеспечени\w*.{0,120}(?:информационн\w* безопасност|"
        r"резервн\w* копирован|бесперебойн\w* работ)|"
        r"эксплуатац\w*.{0,60}(?:IT|ИТ)[- ]?(?:систем|инфраструктур)|"
        r"\bIT\s+infrastructure\b|\binformation\s+security\b|"
        r"\bbusiness\s+continuity\b|\bbackup\w*\b)",
        re.I,
    ),
}
EXECUTIVE_OPERATIONS_PATTERNS = {
    "title": re.compile(
        r"(Title:.{0,100}(?:executive.{0,20}assistant|chief\s+of\s+staff|"
        r"координатор.{0,30}(?:CEO|генеральн)|бизнес[- ]?ассистент)|"
        r"\bexecutive\s+support\b)",
        re.I,
    ),
    "executive_support": re.compile(
        r"(операционн\w*.{0,50}(?:опор|поддерж)\w*.{0,80}"
        r"(?:CEO|генеральн\w* директор|руководител)|"
        r"готовить.{0,40}(?:CEO|генеральн\w* директор).{0,40}(?:встреч|решен)|"
        r"работать рядом с (?:руководител|топ[- ]?команд)|"
        r"поддержк\w*.{0,40}(?:CEO|генеральн\w* директор))",
        re.I,
    ),
    "executive_cadence": re.compile(
        r"(briefing|брифинг|follow[- ]?up|систем\w*.{0,30}поручен|"
        r"входящ\w*.{0,60}(?:CEO|руководител)|"
        r"офис\w*.{0,30}генеральн\w* директор)",
        re.I,
    ),
}
BUSINESS_ANALYSIS_PATTERNS = {
    "title": re.compile(
        r"(Title:.{0,120}(?:business\s+analyst|system\s+analyst|"
        r"бизнес[- ]?аналитик|системн\w*\s+аналитик)|"
        r"(?:senior|lead)\s+business\s+analyst)",
        re.I,
    ),
    "analysis_artifacts": re.compile(
        r"(BRD|TDR|user\s+stor(?:y|ies)|"
        r"бизнес[- ]?требован\w*|функциональн\w*\s+требован\w*|"
        r"моделирован\w*.{0,40}бизнес[- ]?процесс|BPMN|UML)",
        re.I,
    ),
    "solution_analysis": re.compile(
        r"(проектирован\w*.{0,60}решен\w*|"
        r"анализ\w*.{0,60}(?:требован|бизнес[- ]?процесс)|"
        r"solution\s+design|requirements?\s+(?:analysis|elicitation)|"
        r"expert\s+support.{0,80}(?:implementation|project))",
        re.I,
    ),
}

BUSINESS_FUNCTION_PATTERNS = {
    "career_domain": re.compile(
        r"(карьер\w*|трудоустрой\w*|employment|career\s+(?:center|service|track))",
        re.I,
    ),
    "business_metrics": re.compile(
        r"(конверси\w*.{0,80}трудоустрой|воронк\w*.{0,80}трудоустрой|"
        r"метрик\w*.{0,80}(?:карьер|трудоустрой)|"
        r"эффективност\w*.{0,80}(?:инициатив|трудоустрой)|"
        r"employment.{0,60}(?:metric|conversion|funnel))",
        re.I,
    ),
    "partner_process": re.compile(
        r"(партнер\w*.{0,80}(?:трудоустрой|работодател)|"
        r"работодател\w*.{0,80}(?:партнер|обратн\w* связ)|"
        r"оптимизир\w*.{0,80}процесс\w*.{0,80}трудоустрой|"
        r"employer.{0,80}(?:partner|feedback)|employment.{0,80}process)",
        re.I,
    ),
    "journey_research": re.compile(
        r"(\bCJM\b|путь\w*.{0,40}студент|"
        r"исследова\w*.{0,80}(?:практик|карьер)|"
        r"обратн\w*.{0,50}связ\w*.{0,80}(?:студент|выпускник)|"
        r"career.{0,80}(?:research|journey|feedback))",
        re.I,
    ),
    "supporting_it": re.compile(
        r"((?:разработчик|аналитик)\w*.{0,100}(?:CRM|кабинет)|"
        r"(?:CRM|кабинет)\w*.{0,100}(?:разработчик|аналитик)|"
        r"developers?.{0,100}(?:CRM|portal|cabinet)|"
        r"(?:CRM|portal).{0,100}developers?)",
        re.I,
    ),
}
END_TO_END_IT_DELIVERY_RE = re.compile(
    r"("
    r"(?:разработк\w*|development).{0,140}(?:тестирован\w*|testing)"
    r".{0,140}(?:релиз\w*|production|deployment|ввод\w*.{0,30}эксплуатац)"
    r"|(?:требован\w*|requirements?).{0,180}(?:архитектур\w*|architecture)"
    r".{0,180}(?:релиз\w*|production|deployment)"
    r"|(?:SDLC|software\s+delivery).{0,160}(?:релиз|production|deployment)"
    r")",
    re.I | re.S,
)


MANDATORY_DOMAIN_EXPERTISE_RE = re.compile(
    r"("
    r"(?:опыт|пониман\w*|знан\w*|экспертиз\w*|навык\w*|разбира\w*)"
    r".{0,120}(?:печатн\w*\s+плат|конструкторск\w*\s+документац|"
    r"схемотехн\w*|электроник\w*|радиоэлектрон\w*|"
    r"аппаратн\w*\s+част|\bPCB\b|\bhardware\b)"
    r"|(?:experience|knowledge|understanding|expertise|familiarity)"
    r".{0,120}(?:printed\s+circuit|\bPCB\b|electronics|schematic|"
    r"engineering\s+documentation|hardware)"
    r")",
    re.I,
)
PLACEHOLDER_EVIDENCE = {
    "RECRUITER_VISIBLE_RESUME",
    "RECRUITER VISIBLE RESUME",
    "CANDIDATE_FACTS",
    "CANDIDATE FACTS",
    "RESUME",
    "CV",
}


def _it_function_leadership_signals(vacancy: str) -> list[str]:
    text = vacancy or ""
    return [
        key
        for key, pattern in IT_FUNCTION_LEADERSHIP_PATTERNS.items()
        if pattern.search(text)
    ]


def _executive_operations_signals(vacancy: str) -> list[str]:
    text = vacancy or ""
    return [
        key
        for key, pattern in EXECUTIVE_OPERATIONS_PATTERNS.items()
        if pattern.search(text)
    ]


def _business_analysis_signals(vacancy: str) -> list[str]:
    text = vacancy or ""
    return [
        key
        for key, pattern in BUSINESS_ANALYSIS_PATTERNS.items()
        if pattern.search(text)
    ]


def _business_function_signals(vacancy: str) -> list[str]:
    text = vacancy or ""
    return [
        key
        for key, pattern in BUSINESS_FUNCTION_PATTERNS.items()
        if pattern.search(text)
    ]


def extraction_consistency_issues(
    extraction: "CleanShadowExtraction",
    vacancy: str = "",
) -> list[str]:
    issues: list[str] = []

    function_signals = _it_function_leadership_signals(vacancy)
    if (
        extraction.primary_object in {"project", "program"}
        and extraction.role_family_primary in PROJECT_LIKE_FAMILIES
        and len(function_signals) >= 2
    ):
        issues.append(
            "vacancy has multiple ongoing IT-function ownership signals "
            f"({', '.join(function_signals)}); re-check whether primary_object "
            "must be it_function / IT_FUNCTION_LEADERSHIP instead of project delivery"
        )

    executive_signals = _executive_operations_signals(vacancy)
    if (
        extraction.primary_object in {"project", "program"}
        and extraction.role_family_primary in PROJECT_LIKE_FAMILIES
        and len(executive_signals) >= 2
    ):
        issues.append(
            "vacancy has multiple executive-support/operating-cadence signals "
            f"({', '.join(executive_signals)}); re-check whether primary_object "
            "must be executive_support / EXECUTIVE_OPERATIONS instead of project delivery"
        )

    analysis_signals = _business_analysis_signals(vacancy)
    if (
        extraction.primary_object in {"project", "program"}
        and extraction.role_family_primary in PROJECT_LIKE_FAMILIES
        and len(analysis_signals) >= 2
    ):
        issues.append(
            "vacancy has multiple business/system-analysis signals "
            f"({', '.join(analysis_signals)}); re-check whether the primary "
            "outcome is requirements/process/solution analysis without delivery "
            "ownership. If so use primary_object=business_analysis and "
            "role_family=BUSINESS_ANALYSIS instead of project delivery"
        )

    business_function_signals = _business_function_signals(vacancy)
    business_outcome_signals = [
        signal
        for signal in business_function_signals
        if signal != "supporting_it"
    ]
    if (
        extraction.primary_object in {"project", "program"}
        and extraction.role_family_primary in PROJECT_LIKE_FAMILIES
        and len(business_outcome_signals) >= 3
        and "supporting_it" in business_function_signals
        and not END_TO_END_IT_DELIVERY_RE.search(vacancy or "")
    ):
        issues.append(
            "vacancy has dominant non-IT business-function outcome signals "
            f"({', '.join(business_function_signals)}) while digital tooling "
            "appears enabling rather than the primary delivery object; re-check "
            "whether primary_object must be business_function and role_family "
            "BUSINESS_FUNCTION (or another noncore family) instead "
            "of project delivery. Keep PROJECT_* only with explicit E2E IT "
            "system delivery ownership across software lifecycle/release."
        )

    if (
        extraction.primary_object == "project"
        and extraction.role_family_primary in PROJECT_LIKE_FAMILIES
        and extraction.technical_context_fit == "weak"
    ):
        issues.append(
            "project-like classification has weak technical context; re-check "
            "whether this is a NON_IT_PROJECT/non_it_asset rather than a CLEAN "
            "IT/technical project"
        )

    if (
        extraction.primary_object == "project"
        and extraction.project_lifecycle_ownership
        in {"full", "substantial"}
        and extraction.role_family_primary
        not in PROJECT_LIKE_FAMILIES
        and extraction.role_family_primary != "NON_IT_PROJECT"
    ):
        issues.append(
            "primary_object=project with full/substantial lifecycle "
            "must use a project/program delivery role family"
        )

    if (
        extraction.primary_object == "program"
        and extraction.role_family_primary
        not in PROJECT_LIKE_FAMILIES
    ):
        issues.append(
            "primary_object=program must use PROGRAM_DELIVERY or "
            "another project-delivery family"
        )

    for index, requirement in enumerate(
        extraction.requirements,
        start=1,
    ):
        source = requirement.source_text or ""
        normalized_source = source.strip().lower()
        if (
            not normalized_source
            or normalized_source
            in {"not specified", "не указано", "n/a", "none"}
        ):
            issues.append(
                f"requirement #{index} has no vacancy source and must be removed"
            )

        if (
            requirement.category == "work_auth"
            and not WORK_AUTH_SOURCE_RE.search(source)
        ):
            issues.append(
                f"requirement #{index} is not work_auth: {source[:120]}"
            )

        if (
            requirement.criticality == "non_negotiable"
            and requirement.category == "other"
            and MANDATORY_DOMAIN_EXPERTISE_RE.search(source)
        ):
            issues.append(
                f"requirement #{index} contains mandatory domain-specific "
                "technical expertise; classify as exact_domain (or exact_stack "
                "only for an explicitly required concrete technology/tool) "
                f"instead of other: {source[:160]}"
            )

        evidence = (requirement.candidate_evidence or "").strip().upper()
        if evidence in PLACEHOLDER_EVIDENCE:
            issues.append(
                f"requirement #{index} candidate_evidence is a placeholder; "
                "use a concrete visible resume fact or leave it empty"
            )

    return issues


def _parse_extraction_response(response) -> "CleanShadowExtraction":
    raw = _response_text(response).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = _extract_json(raw)
    return CleanShadowExtraction.model_validate(payload)


class CleanShadowEvaluator:
    def __init__(
        self,
        llm: LLMProvider | None = None,
        *,
        learned_patterns: list[dict] | None = None,
    ) -> None:
        self.llm = llm or LLMProvider()
        self.learned_patterns = [
            {
                "pattern_key": str(item.get("pattern_key") or "").strip(),
                "pattern_type": str(item.get("pattern_type") or "").strip(),
                "statement": str(item.get("statement") or "").strip(),
                "support_count": int(item.get("support_count") or 0),
                "confidence_score": item.get("confidence_score"),
            }
            for item in (learned_patterns or [])
            if str(item.get("pattern_key") or "").strip()
            and str(item.get("statement") or "").strip()
        ]

    def _review_recruiter_visibility(
        self,
        *,
        extraction: CleanShadowExtraction,
        recruiter_visible_resume: str,
        vacancy: str,
        cover_letter: str,
    ) -> RecruiterVisibilityReview:
        schema = RecruiterVisibilityReview.model_json_schema()
        requirements = [
            {
                "index": index,
                "name": item.name,
                "criticality": item.criticality,
                "category": item.category,
                "source_text": item.source_text,
            }
            for index, item in enumerate(extraction.requirements)
        ]
        prompt = f"""
Ты отдельный recruiter-visibility auditor. Оцени только то, что реально
увидит рекрутер. У тебя НЕТ доступа к внутреннему профилю кандидата.

Правила:
- для каждого requirement ищи подтверждение только в RECRUITER VISIBLE RESUME
  и CURRENT COVER LETTER;
- CV_DIRECT = факт явно написан в резюме;
- CV_SEMANTIC = формулировка другая, но опыт однозначно эквивалентен;
- COVER_SURFACED = подтверждение есть только в фактически переданном письме;
- UNCONFIRMED = подтверждения в видимых материалах нет;
- candidate_evidence должен быть конкретным коротким фактом из видимого
  резюме/письма, а не названием источника;
- не используй размеры команды, бюджет, AI-agent или другие факты, которых
  нет в видимом резюме/письме;
- если vacancy requirement говорит full lifecycle, а резюме явно говорит
  "full lifecycle ... from requirements to production", это CV_DIRECT/full;
- аналогично явно учитывай видимые budget, risks, timelines, stakeholders,
  cross-functional teams, contractors, development/testing/production;
- preferred domain не превращай в обязательный;
- оцени narrative/recent/seniority/domain/differentiators только по видимому
  резюме. Не штрафуй за отсутствие факта, которого вакансия не требует.

VACANCY:
{vacancy[:26000]}

VACANCY REQUIREMENTS:
{json.dumps(requirements, ensure_ascii=False)[:16000]}

RECRUITER VISIBLE RESUME:
{recruiter_visible_resume[:24000]}

CURRENT COVER LETTER:
{cover_letter[:8000]}
""".strip()
        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            format_schema=schema,
        )
        raw = _response_text(response).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = _extract_json(raw)
        return RecruiterVisibilityReview.model_validate(payload)

    @staticmethod
    def _apply_recruiter_visibility(
        extraction: CleanShadowExtraction,
        review: RecruiterVisibilityReview,
    ) -> None:
        for item in review.requirements:
            if item.index >= len(extraction.requirements):
                continue
            requirement = extraction.requirements[item.index]
            if item.evidence_visibility == "UNCONFIRMED":
                if requirement.evidence_visibility != "INTERNAL_ONLY":
                    requirement.evidence_visibility = "UNCONFIRMED"
                    requirement.match_quality = item.match_quality
                    requirement.candidate_evidence = ""
                continue

            requirement.evidence_visibility = item.evidence_visibility
            requirement.match_quality = item.match_quality
            requirement.candidate_evidence = item.candidate_evidence

        extraction.role_narrative_coherence = (
            review.role_narrative_coherence
        )
        extraction.recent_relevant_evidence = (
            review.recent_relevant_evidence
        )
        extraction.seniority_autonomy_visibility = (
            review.seniority_autonomy_visibility
        )
        extraction.domain_technical_visibility = (
            review.domain_technical_visibility
        )
        extraction.visible_differentiators = (
            review.visible_differentiators
        )
        extraction.cover_surfaced_evidence = (
            review.cover_surfaced_evidence
        )
        extraction.top_invite_reasons = review.top_invite_reasons
        extraction.invite_risks = review.invite_risks

    def _review_learned_patterns(
        self,
        *,
        extraction: CleanShadowExtraction,
        vacancy: str,
    ) -> LearnedPatternReview | None:
        if not self.learned_patterns:
            return None

        schema = LearnedPatternReview.model_json_schema()
        compact_patterns = [
            {
                "pattern_key": item["pattern_key"],
                "pattern_type": item["pattern_type"],
                "statement": item["statement"],
                "support_count": item["support_count"],
                "confidence_score": item["confidence_score"],
            }
            for item in self.learned_patterns[:30]
        ]
        extraction_summary = {
            "role_family_primary": extraction.role_family_primary,
            "primary_object": extraction.primary_object,
            "project_lifecycle_ownership": (
                extraction.project_lifecycle_ownership
            ),
            "complexity_seniority": extraction.complexity_seniority,
            "technical_context_fit": extraction.technical_context_fit,
            "domain_affinity": extraction.domain_affinity,
            "change_outcome_fit": extraction.change_outcome_fit,
            "requirements": [
                {
                    "name": item.name,
                    "criticality": item.criticality,
                    "category": item.category,
                    "evidence_visibility": item.evidence_visibility,
                    "match_quality": item.match_quality,
                }
                for item in extraction.requirements
            ],
        }

        prompt = f"""
Ты calibration reviewer. Тебе уже дали завершённую structured-оценку
вакансии и накопленные learned patterns из прошлых исходов.

Твоя задача ТОЛЬКО найти релевантные historical signals для объяснения
решения. Запрещено менять или переопределять:
- role_family / primary_object / lifecycle;
- requirement categories/criticality/evidence;
- hard stops;
- FIT/INVITE score, thresholds или routing.

Правила:
- pattern не является фактом о текущей вакансии;
- не придумывай evidence, которого нет в VACANCY/EXTRACTION;
- используй pattern только если он действительно похож на текущий кейс;
- positive_signals и risks формулируй как мягкие historical observations,
  а не как гарантии приглашения/отказа;
- relevant_pattern_keys может содержать только ключи из LEARNED PATTERNS;
- если релевантных patterns нет, верни пустые списки.

VACANCY:
{vacancy[:22000]}

STRUCTURED EXTRACTION:
{json.dumps(extraction_summary, ensure_ascii=False)[:14000]}

LEARNED PATTERNS:
{json.dumps(compact_patterns, ensure_ascii=False)[:12000]}
""".strip()

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            format_schema=schema,
        )
        raw = _response_text(response).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = _extract_json(raw)
        return LearnedPatternReview.model_validate(payload)

    def _apply_learned_pattern_review(
        self,
        extraction: CleanShadowExtraction,
        review: LearnedPatternReview,
    ) -> None:
        allowed = {
            item["pattern_key"]
            for item in self.learned_patterns
        }
        keys: list[str] = []
        for key in review.relevant_pattern_keys:
            if key in allowed and key not in keys:
                keys.append(key)

        extraction.learned_pattern_keys = keys
        if not keys:
            extraction.learned_positive_signals = []
            extraction.learned_risks = []
            return

        extraction.learned_positive_signals = list(
            review.positive_signals[:8]
        )
        extraction.learned_risks = list(review.risks[:8])

        for signal in extraction.learned_positive_signals:
            extraction.top_invite_reasons.append(
                f"[learned] {signal}"
            )
        for risk in extraction.learned_risks:
            extraction.invite_risks.append(
                f"[learned] {risk}"
            )

    def evaluate(
        self,
        *,
        candidate_facts: str,
        recruiter_visible_resume: str,
        vacancy: str,
        cover_letter: str = "",
    ) -> CleanShadowExtraction:
        schema = CleanShadowExtraction.model_json_schema()
        prompt = f"""
Ты работаешь в SHADOW-режиме оценки вакансий. Ничего не решай за production
pipeline: не выдавай APPLY/REJECT и не ставь числовой score.

Нужно структурировать вакансию для отдельного CLEAN-оценщика.

КОНТЕКСТ:
- текущее позиционирование кандидата: Руководитель сложных IT-проектов;
- Delivery/Technical PM/Program Delivery допустимы, если фактический scope
  является end-to-end управлением IT-проектом/связанной программой;
- Product, Engineering Management, PMO/Portfolio governance, IT-function
  leadership, Sales/Account, Data/ML functional leadership, business/system
  analysis, non-IT business-function operations, executive support/Chief of
  Staff operations и non-IT project являются отдельными role families, даже
  если внутри есть сроки/команды;
- role family определяй по primary object/outcome, а не по title;
- full/substantial lifecycle ownership сам по себе НЕ делает роль CLEAN.
  Если primary_object=project и проект материально относится к IT/software/
  digital/интеграциям/инфраструктуре/техническому delivery, используй
  PROJECT_CORE / PROJECT_DELIVERY / TECHNICAL_PROJECT либо смежный delivery.
  Если это стройка, транспортное планирование, пожарная безопасность или другой
  non-IT предметный проект, используй NON_IT_PROJECT (primary_object может быть
  non_it_asset); не повышай его до PROJECT_CORE только из-за полного lifecycle;
- PROGRAM_DELIVERY используй для связанной программы/набора проектов с
  delivery ownership; IT_FUNCTION_LEADERSHIP только для постоянной IT-функции,
  оргструктуры или подразделения, где проект не является primary outcome;
- для смешанной роли смотри, что останется после завершения отдельных проектов:
  если вакансия одновременно владеет IT-стратегией/IT-направлением,
  IT-инфраструктурой/ИБ/бесперебойностью и IT-командой/сотрудниками, это
  ongoing IT-function ownership. Тогда primary_object=it_function и
  role_family=IT_FUNCTION_LEADERSHIP, даже если внутри много трансформационных
  и интеграционных проектов. Одного случайного упоминания этих тем недостаточно;
- Business/System Analyst не становится PROJECT_CORE только потому, что ведёт
  requirements lifecycle, roadmap, BRD/TDR/User Stories, stakeholder workshops,
  solution design или сопровождает реализацию. Если primary outcome = сбор и
  анализ требований, моделирование процессов/решения и аналитическая поддержка
  реализации БЕЗ ownership сроков/бюджета/команды/delivery outcome, используй
  primary_object=business_analysis и role_family=BUSINESS_ANALYSIS. Если же
  фактический scope действительно владеет E2E delivery отдельного IT-проекта,
  project family допустима независимо от title;
- non-IT бизнес-функция не становится IT PROJECT_CORE только потому, что
  использует CRM/кабинет/автоматизацию и координируется с разработчиками.
  Если primary outcome = развитие HR/карьеры/трудоустройства, маркетинга,
  продаж, обучения или другого бизнес-процесса: его метрик/CJM, партнёров,
  исследований и операционного процесса, а digital tooling лишь помогает
  функции, используй primary_object=business_function и
  role_family=BUSINESS_FUNCTION (либо другой подходящий noncore
  family). PROJECT_* допустим только когда primary outcome = E2E delivery самой
  IT-системы с явным ownership software lifecycle/release, а бизнес-функция
  является контекстом;
- если primary outcome роли = операционная поддержка CEO/топ-руководителя:
  briefing к встречам и решениям, поток входящей информации, система поручений,
  follow-up, executive cadence, организация работы офиса руководителя, это
  primary_object=executive_support и role_family=EXECUTIVE_OPERATIONS.
  Наличие декомпозиции, сроков, рисков, stakeholder coordination и даже
  отдельных инициатив НЕ превращает такую роль в PROJECT_CORE;
- Chief of Staff / Executive Assistant / бизнес-ассистент / координатор CEO
  классифицируй по фактическому primary outcome. Если это управление отдельной
  IT-программой с E2E delivery, project/program family допустима; если это
  операционный контур вокруг руководителя, EXECUTIVE_OPERATIONS;
- если в rationale ты сам пишешь, что core function = end-to-end IT project
  management, role_family обязан быть project/program delivery;
- категории requirements используй строго:
  * language = только требование к человеческому языку и уровню владения;
  * hands_on = только обязательная личная hands-on работа кандидата
    (код, конфигурация, моделирование и т.п.), не управление разработкой;
  * exact_stack = обязательная конкретная технология/стек;
  * exact_domain = обязательная предметная/отраслевая экспертиза, включая
    явно требуемое знание специфических процессов или артефактов домена
    (например печатные платы, схемотехника, конструкторская документация),
    даже если сама роль является PM. Общие SDLC/PM-навыки остаются other;
  * education_clearance = диплом/образование/сертификат/лицензия/допуск;
  * work_auth = ТОЛЬКО гражданство, право на работу, виза, релокация или
    географическое ограничение. Сроки, бюджет, риски, stakeholder management,
    lifecycle, подрядчики и команда всегда category=other;
  * other = обычные PM/delivery требования;
- candidate_evidence должен содержать конкретный факт из видимого резюме
  (например "full lifecycle from requirements to production", "planning,
  timelines, budget, risks"). Нельзя писать заглушки RECRUITER_VISIBLE_RESUME,
  CANDIDATE_FACTS, RESUME или CV;
- INTERNAL candidate facts НЕ считаются видимыми рекрутеру;
- для invite evidence используй только RECRUITER VISIBLE RESUME и фактически
  переданное COVER LETTER;
- если факт подтверждён только во внутренних данных, ставь INTERNAL_ONLY;
- если требование не подтверждено, ставь UNCONFIRMED, не придумывай отсутствие
  навыка как факт;
- non_negotiable используй только когда текст вакансии явно делает требование
  обязательным;
- preferred/context не превращай в must-have;
- не создавай requirement, если в VACANCY нет соответствующего требования:
  source_text="not specified"/"не указано" запрещён, такой item нужно убрать;
- простое упоминание AI/Big Data/Cloud в описании предметной области не является
  requirement само по себе без формулировки ожидания от кандидата;
- source_text должен быть короткой опорой из вакансии, без длинных цитат.

CANDIDATE FACTS (capability/FIT only):
{candidate_facts[:22000]}

RECRUITER VISIBLE CLEAN RESUME (INVITE evidence):
{recruiter_visible_resume[:22000]}

CURRENT COVER LETTER, если уже существует:
{cover_letter[:8000]}

VACANCY:
{vacancy[:26000]}
""".strip()

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            format_schema=schema,
        )
        extraction = _parse_extraction_response(response)
        issues = extraction_consistency_issues(
            extraction,
            vacancy=vacancy,
        )

        if issues:
            repair_prompt = (
                prompt
                + "\n\nПРЕДЫДУЩИЙ STRUCTURED ОТВЕТ:\n"
                + extraction.model_dump_json()
                + "\n\nVALIDATION ERRORS:\n- "
                + "\n- ".join(issues)
                + "\n\nИсправь structured extraction. "
                "Не защищай предыдущий ответ. Сверь каждое поле с VACANCY "
                "и RECRUITER VISIBLE RESUME. Верни только JSON по схеме."
            )
            repaired_response = self.llm.chat(
                messages=[
                    {"role": "user", "content": repair_prompt},
                ],
                format_schema=schema,
            )
            repaired = _parse_extraction_response(
                repaired_response
            )
            repaired_issues = extraction_consistency_issues(
                repaired,
                vacancy=vacancy,
            )
            if repaired_issues:
                raise RuntimeError(
                    "CLEAN shadow extraction consistency failed after repair: "
                    + "; ".join(repaired_issues)
                )
            extraction = repaired

        if effective_clean_role_class(extraction) in {
            "core",
            "adjacent",
            "unknown",
        }:
            visibility_review = self._review_recruiter_visibility(
                extraction=extraction,
                recruiter_visible_resume=recruiter_visible_resume,
                vacancy=vacancy,
                cover_letter=cover_letter,
            )
            self._apply_recruiter_visibility(
                extraction,
                visibility_review,
            )

            pattern_review = self._review_learned_patterns(
                extraction=extraction,
                vacancy=vacancy,
            )
            if pattern_review is not None:
                self._apply_learned_pattern_review(
                    extraction,
                    pattern_review,
                )

        return extraction


FIT_ROLE = {
    "core": 40,
    "adjacent": 32,
    "noncore": 0,
    "unknown": 12,
}
FIT_LIFECYCLE = {
    "full": 20,
    "substantial": 15,
    "partial": 7,
    "none": 0,
}
FIT_COMPLEXITY = {
    "strong": 15,
    "good": 12,
    "acceptable": 8,
    "mismatch": 2,
}
FIT_TECH = {
    "strong": 10,
    "transferable": 7,
    "generic": 5,
    "weak": 0,
}
FIT_DOMAIN = {
    "preferred": 10,
    "direct": 10,
    "transferable": 7,
    "weak": 3,
    "unwanted": 0,
}
FIT_CHANGE = {
    "strong": 5,
    "substantial": 4,
    "weak": 2,
    "none": 0,
}

INVITE_NARRATIVE = {
    "strong": 20,
    "good": 15,
    "strained": 8,
    "incoherent": 0,
}
INVITE_RECENT = {
    "strong_recent": 15,
    "recent_adjacent": 11,
    "older_only": 5,
    "none": 0,
}
INVITE_SENIORITY = {
    "strong": 10,
    "good": 8,
    "weak": 4,
    "mismatch": 0,
}
INVITE_DOMAIN = {
    "direct": 10,
    "transferable": 7,
    "weak": 3,
    "none": 0,
}
INVITE_DIFF = {
    "strong": 5,
    "generic": 3,
    "none": 0,
}
INVITE_COVER = {
    "effective": 5,
    "neutral": 2,
    "none": 0,
    "oversell": 0,
}
REQ_WEIGHT = {
    "non_negotiable": 3.0,
    "core": 3.0,
    "preferred": 1.0,
    "context": 0.0,
}
VISIBILITY_CREDIT = {
    "CV_DIRECT": 1.0,
    "CV_SEMANTIC": 0.9,
    "COVER_SURFACED": 0.65,
    "INTERNAL_ONLY": 0.0,
    "UNCONFIRMED": 0.0,
}
MATCH_CREDIT = {
    "full": 1.0,
    "partial": 0.5,
    "none": 0.0,
}

GLOBAL_STOP_CODES = {
    "salary_floor",
    "unwanted_domain",
    "location_work_auth",
    "mandatory_education_clearance",
}
REQUIREMENT_STOP_CATEGORIES = {
    "language": "mandatory_language",
    "hands_on": "mandatory_hands_on",
    "exact_stack": "mandatory_exact_stack",
    "exact_domain": "mandatory_exact_domain",
    "education_clearance": "mandatory_education_clearance",
}


def score_fit(extraction: CleanShadowExtraction) -> int:
    score = (
        FIT_ROLE[effective_clean_role_class(extraction)]
        + FIT_LIFECYCLE[extraction.project_lifecycle_ownership]
        + FIT_COMPLEXITY[extraction.complexity_seniority]
        + FIT_TECH[extraction.technical_context_fit]
        + FIT_DOMAIN[extraction.domain_affinity]
        + FIT_CHANGE[extraction.change_outcome_fit]
    )
    return max(0, min(100, int(round(score))))


def _requirement_component(requirements: list[RequirementEvidence]) -> int:
    denominator = 0.0
    earned = 0.0
    for item in requirements:
        weight = REQ_WEIGHT[item.criticality]
        if weight <= 0:
            continue
        denominator += weight
        earned += (
            weight
            * VISIBILITY_CREDIT[item.evidence_visibility]
            * MATCH_CREDIT[item.match_quality]
        )
    if denominator <= 0:
        return 0
    return max(0, min(35, int(round(35 * earned / denominator))))


def score_invite(extraction: CleanShadowExtraction) -> int:
    score = (
        _requirement_component(extraction.requirements)
        + INVITE_NARRATIVE[extraction.role_narrative_coherence]
        + INVITE_RECENT[extraction.recent_relevant_evidence]
        + INVITE_SENIORITY[extraction.seniority_autonomy_visibility]
        + INVITE_DOMAIN[extraction.domain_technical_visibility]
        + INVITE_DIFF[extraction.visible_differentiators]
        + INVITE_COVER[extraction.cover_surfaced_evidence]
    )
    return max(0, min(100, int(round(score))))


def _salary_stop(
    *,
    salary_from: int | None,
    salary_to: int | None,
    salary_currency: str | None,
) -> bool:
    currency = (salary_currency or "").upper().strip()
    if currency not in {"RUB", "RUR", "РУБ", "₽"}:
        return False
    if salary_to is not None and salary_to < 300_000:
        return True
    if salary_from is not None and salary_to is not None:
        return max(salary_from, salary_to) < 300_000
    return False


def collect_hard_stops(
    extraction: CleanShadowExtraction,
    *,
    salary_from: int | None,
    salary_to: int | None,
    salary_currency: str | None,
    description: str,
) -> tuple[str, ...]:
    stops: list[str] = []

    if len((description or "").strip()) < 80:
        stops.append("data_insufficient")

    if _salary_stop(
        salary_from=salary_from,
        salary_to=salary_to,
        salary_currency=salary_currency,
    ):
        stops.append("salary_floor")

    if extraction.unwanted_domain_status == "fail":
        stops.append("unwanted_domain")

    if extraction.location_work_auth_status == "fail":
        stops.append("location_work_auth")

    if extraction.complexity_seniority == "mismatch":
        stops.append("seniority_mismatch")

    if extraction.technical_context_fit == "weak":
        stops.append("technical_context_weak")

    if effective_clean_role_class(extraction) == "noncore":
        stops.append("role_family_noncore")

    for req in extraction.requirements:
        if req.criticality != "non_negotiable":
            continue
        if req.match_quality != "none":
            continue
        code = REQUIREMENT_STOP_CATEGORIES.get(req.category)
        if code:
            stops.append(code)
        else:
            # An explicitly mandatory requirement that the candidate does not
            # satisfy must never silently pass CLEAN merely because the model
            # left its category as generic "other".
            stops.append("mandatory_requirement_missing")

    return tuple(dict.fromkeys(stops))


def route_shadow(
    *,
    fit_score: int,
    invite_score: int,
    hard_stops: tuple[str, ...],
    role_confidence: float,
) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []

    if "data_insufficient" in hard_stops:
        return "HOLD", ("DATA_INSUFFICIENT",)

    if any(code in GLOBAL_STOP_CODES for code in hard_stops):
        return "SKIP", tuple(f"HARD_STOP:{code}" for code in hard_stops)

    if hard_stops:
        if fit_score >= 60:
            return "OLD_REVIEW", tuple(f"CLEAN_STOP:{code}" for code in hard_stops)
        return "SKIP", tuple(f"HARD_STOP:{code}" for code in hard_stops)

    if role_confidence < 0.55:
        return "REVIEW", ("LOW_ROLE_CONFIDENCE",)

    if fit_score >= 82 and invite_score >= 82 and role_confidence >= 0.75:
        reasons.extend(["FIT_STRONG", "INVITE_STRONG"])
        return "CLEAN_STRONG", tuple(reasons)

    if fit_score >= 78 and invite_score >= 72:
        reasons.append("CLEAN_BORDERLINE")
        return "CLEAN_REVIEW", tuple(reasons)

    if fit_score >= 70 and invite_score >= 65:
        return "OLD_STRONG", ("OLD_STRONG_BAND",)

    if fit_score >= 60 and invite_score >= 55:
        return "OLD_REVIEW", ("OLD_REVIEW_BAND",)

    return "SKIP", ("LOW_FIT_OR_INVITE",)


def build_shadow_scores(
    extraction: CleanShadowExtraction,
    *,
    salary_from: int | None,
    salary_to: int | None,
    salary_currency: str | None,
    description: str,
) -> ShadowScores:
    fit = score_fit(extraction)
    invite_raw = score_invite(extraction)
    stops = collect_hard_stops(
        extraction,
        salary_from=salary_from,
        salary_to=salary_to,
        salary_currency=salary_currency,
        description=description,
    )
    route, reasons = route_shadow(
        fit_score=fit,
        invite_score=invite_raw,
        hard_stops=stops,
        role_confidence=extraction.role_confidence,
    )
    invite = None if stops else invite_raw
    return ShadowScores(
        fit_score=fit,
        invite_score=invite,
        hard_stops=stops,
        routing_class=route,
        route_reason_codes=reasons,
    )


def normalize_company_key(company: str | None) -> str:
    value = (company or "").lower().replace("ё", "е")
    value = re.sub(r"\b(ооо|пао|ао|зао|оао|ип)\b", " ", value)
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())
