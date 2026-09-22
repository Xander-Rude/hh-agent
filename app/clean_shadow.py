from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm import LLMProvider


PROMPT_VERSION = "clean-shadow-prompt-v1"
SCORING_VERSION = "clean-shadow-score-v2"
GATE_VERSION = "clean-shadow-gates-v2"
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
    "NON_IT_PROJECT",
}


def effective_clean_role_class(
    extraction: "CleanShadowExtraction",
) -> str:
    family = extraction.role_family_primary
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


class CleanShadowEvaluator:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm or LLMProvider()

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
  leadership, Sales/Account, Data/ML functional leadership и non-IT project
  являются отдельными role families, даже если внутри есть сроки/команды;
- role family определяй по primary object/outcome, а не по title;
- INTERNAL candidate facts НЕ считаются видимыми рекрутеру;
- для invite evidence используй только RECRUITER VISIBLE RESUME и фактически
  переданное COVER LETTER;
- если факт подтверждён только во внутренних данных, ставь INTERNAL_ONLY;
- если требование не подтверждено, ставь UNCONFIRMED, не придумывай отсутствие
  навыка как факт;
- non_negotiable используй только когда текст вакансии явно делает требование
  обязательным;
- preferred/context не превращай в must-have;
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
        raw = _response_text(response).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = _extract_json(raw)
        return CleanShadowExtraction.model_validate(payload)


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
    "work_auth": "location_work_auth",
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
