from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clean_shadow import (
    COMPANY_POLICY_VERSION,
    GATE_VERSION,
    PROMPT_VERSION,
    ROUTING_VERSION,
    SCORING_VERSION,
)
from app.db import CleanShadowAssessment
from app.strategy_memory import get_active_memory


CANDIDATE_PROFILE_VERSION = os.getenv(
    "CLEAN_CANDIDATE_PROFILE_VERSION",
    "candidate-facts-v2-2026-09-26",
)
RECRUITER_RESUME_VERSION = os.getenv(
    "CLEAN_RECRUITER_RESUME_VERSION",
    "clean-hh-2026-09-26-ats-final",
)

CLEAN_ELIGIBLE_ROUTES = {"CLEAN_STRONG", "CLEAN_REVIEW"}


@dataclass(frozen=True)
class CleanPolicyContext:
    candidate_profile_version: str
    recruiter_resume_version: str
    prompt_version: str
    scoring_version: str
    gate_version: str
    routing_version: str
    company_policy_version: str
    learned_patterns_version: str | None


@dataclass(frozen=True)
class CleanEligibility:
    eligible: bool
    reason: str
    assessment: CleanShadowAssessment | None


def current_learned_patterns_version() -> str | None:
    memory = get_active_memory()
    if not memory:
        return None

    version = memory.get("version") or {}
    version_number = version.get("version_number")
    content_hash = str(version.get("content_hash") or "").strip()
    if version_number is None or not content_hash:
        return None

    return (
        f"strategy-memory-v1:{int(version_number)}:"
        f"{content_hash[:16]}"
    )


def current_policy_context() -> CleanPolicyContext:
    return CleanPolicyContext(
        candidate_profile_version=CANDIDATE_PROFILE_VERSION,
        recruiter_resume_version=RECRUITER_RESUME_VERSION,
        prompt_version=PROMPT_VERSION,
        scoring_version=SCORING_VERSION,
        gate_version=GATE_VERSION,
        routing_version=ROUTING_VERSION,
        company_policy_version=COMPANY_POLICY_VERSION,
        learned_patterns_version=current_learned_patterns_version(),
    )


def assessment_version_filters(
    context: CleanPolicyContext,
):
    filters = [
        CleanShadowAssessment.status == "ok",
        (
            CleanShadowAssessment.candidate_profile_version
            == context.candidate_profile_version
        ),
        (
            CleanShadowAssessment.recruiter_resume_version
            == context.recruiter_resume_version
        ),
        CleanShadowAssessment.prompt_version == context.prompt_version,
        CleanShadowAssessment.scoring_version == context.scoring_version,
        CleanShadowAssessment.gate_version == context.gate_version,
        CleanShadowAssessment.routing_version == context.routing_version,
        (
            CleanShadowAssessment.company_policy_version
            == context.company_policy_version
        ),
    ]
    if context.learned_patterns_version is None:
        filters.append(
            CleanShadowAssessment.learned_patterns_version.is_(None)
        )
    else:
        filters.append(
            CleanShadowAssessment.learned_patterns_version
            == context.learned_patterns_version
        )
    return tuple(filters)


def current_clean_assessment(
    session: Session,
    vacancy_id: int,
    *,
    context: CleanPolicyContext | None = None,
) -> CleanShadowAssessment | None:
    policy = context or current_policy_context()
    return session.scalar(
        select(CleanShadowAssessment)
        .where(
            CleanShadowAssessment.vacancy_id == vacancy_id,
            *assessment_version_filters(policy),
        )
        .order_by(CleanShadowAssessment.id.desc())
        .limit(1)
    )


def clean_eligibility(
    session: Session,
    vacancy_id: int,
    *,
    context: CleanPolicyContext | None = None,
) -> CleanEligibility:
    assessment = current_clean_assessment(
        session,
        vacancy_id,
        context=context,
    )
    if assessment is None:
        return CleanEligibility(
            eligible=False,
            reason="missing_current_clean_assessment",
            assessment=None,
        )

    route = str(assessment.routing_class or "").strip()
    if route not in CLEAN_ELIGIBLE_ROUTES:
        return CleanEligibility(
            eligible=False,
            reason=f"routing_class={route or 'NONE'}",
            assessment=assessment,
        )

    return CleanEligibility(
        eligible=True,
        reason=f"routing_class={route}",
        assessment=assessment,
    )
