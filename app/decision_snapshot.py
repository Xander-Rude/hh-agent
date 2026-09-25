from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cover_letter_runtime import (
    calibrate_stored_cover_letter,
    parse_strengths,
)
from hh_accounts import account_resume_id
from app.application_assets import (
    CAREER_PROJECT_RESUME_KEY,
    CAREER_PROJECT_RESUME_TITLE,
)

from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    CleanShadowAssessment,
    Evaluation,
    Vacancy,
)


def get_decision_snapshot(
    session: Session,
    application_id: int,
) -> ApplicationDecisionSnapshot | None:
    return session.scalar(
        select(ApplicationDecisionSnapshot)
        .where(
            ApplicationDecisionSnapshot.application_id
            == application_id
        )
        .order_by(ApplicationDecisionSnapshot.id.desc())
        .limit(1)
    )


def _latest_evaluation(
    session: Session,
    vacancy_id: int,
) -> Evaluation | None:
    return session.scalar(
        select(Evaluation)
        .where(Evaluation.vacancy_id == vacancy_id)
        .where(~Evaluation.model.startswith("hard-filter/"))
        .order_by(
            Evaluation.created_at.desc(),
            Evaluation.id.desc(),
        )
        .limit(1)
    )


def _latest_shadow(
    session: Session,
    vacancy_id: int,
) -> CleanShadowAssessment | None:
    return session.scalar(
        select(CleanShadowAssessment)
        .where(
            CleanShadowAssessment.vacancy_id == vacancy_id,
            CleanShadowAssessment.status == "ok",
        )
        .order_by(CleanShadowAssessment.id.desc())
        .limit(1)
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _vacancy_snapshot(vacancy: Vacancy) -> str:
    payload: dict[str, Any] = {
        "id": vacancy.id,
        "source": vacancy.source,
        "external_id": vacancy.external_id,
        "hh_id": vacancy.hh_id,
        "title": vacancy.title,
        "company": vacancy.company,
        "url": vacancy.url,
        "salary_from": vacancy.salary_from,
        "salary_to": vacancy.salary_to,
        "salary_currency": vacancy.salary_currency,
        "found_at": _iso(vacancy.found_at),
        "published_at": _iso(vacancy.published_at),
    }
    return json.dumps(payload, ensure_ascii=False)


def _application_type(application: Application) -> str:
    if (application.account_key or "old") == "clean":
        return "fresh_clean"
    return "old"


def ensure_decision_snapshot(
    session: Session,
    *,
    application: Application,
    vacancy: Vacancy,
) -> ApplicationDecisionSnapshot:
    """Persist the exact decision state once, at approval time.

    The snapshot is intentionally idempotent and immutable. It captures the
    latest visible legacy evaluation plus any available CLEAN shadow assessment,
    without making shadow routing authoritative yet.
    """
    existing = get_decision_snapshot(
        session,
        application.id,
    )
    if existing is not None:
        return existing

    evaluation = _latest_evaluation(
        session,
        vacancy.id,
    )
    shadow = _latest_shadow(
        session,
        vacancy.id,
    )

    cover_letter = (application.cover_letter or "").strip()
    if evaluation is not None:
        cover_letter = calibrate_stored_cover_letter(
            evaluation.cover_letter,
            parse_strengths(evaluation.strengths),
        ).strip()

        application.cover_letter = cover_letter or None
        application.selected_resume_key = evaluation.selected_resume_key
        application.selected_resume_title = evaluation.selected_resume_title
        application.selected_resume_id = evaluation.selected_resume_id
        application.selected_resume_score = evaluation.selected_resume_score

    account_key = application.account_key or "old"
    vacancy_source = (vacancy.source or "hh").strip().lower()

    if vacancy_source == "hh":
        bound_resume_id = account_resume_id(account_key)
        if bound_resume_id:
            if application.selected_resume_id != bound_resume_id:
                application.selected_resume_score = None
            application.selected_resume_id = bound_resume_id
            application.selected_resume_key = f"hh-{account_key}"
    elif vacancy_source in {"yandex", "vk", "tbank", "ozon"}:
        application.selected_resume_key = CAREER_PROJECT_RESUME_KEY
        application.selected_resume_title = CAREER_PROJECT_RESUME_TITLE
        application.selected_resume_id = None
        application.selected_resume_score = None

    snapshot = ApplicationDecisionSnapshot(
        application_id=application.id,
        vacancy_id=vacancy.id,
        legacy_evaluation_id=(
            evaluation.id
            if evaluation is not None
            else None
        ),
        shadow_assessment_id=(
            shadow.id
            if shadow is not None
            else None
        ),
        account_key=account_key,
        selected_resume_key=application.selected_resume_key,
        selected_resume_title=application.selected_resume_title,
        selected_resume_id=application.selected_resume_id,
        selected_resume_score=application.selected_resume_score,
        application_type=_application_type(application),
        routing_class=(
            shadow.routing_class
            if shadow is not None
            else "LEGACY"
        ),
        route_reason_codes=(
            shadow.route_reason_codes
            if shadow is not None
            else "[]"
        ),
        fit_score=(
            shadow.fit_score
            if shadow is not None
            else None
        ),
        invite_score=(
            shadow.invite_score
            if shadow is not None
            else None
        ),
        hard_stops=(
            shadow.hard_stops
            if shadow is not None
            else "[]"
        ),
        role_family=(
            shadow.role_family
            if shadow is not None
            else None
        ),
        role_confidence_pct=(
            shadow.role_confidence_pct
            if shadow is not None
            else None
        ),
        company_entity_key=(
            shadow.company_entity_key
            if shadow is not None
            else None
        ),
        company_rank=(
            shadow.company_rank
            if shadow is not None
            else None
        ),
        company_state=(
            shadow.company_state
            if shadow is not None
            else None
        ),
        candidate_profile_version=(
            shadow.candidate_profile_version
            if shadow is not None
            else None
        ),
        recruiter_resume_version=(
            shadow.recruiter_resume_version
            if shadow is not None
            else None
        ),
        prompt_version=(
            shadow.prompt_version
            if shadow is not None
            else None
        ),
        scoring_version=(
            shadow.scoring_version
            if shadow is not None
            else None
        ),
        gate_version=(
            shadow.gate_version
            if shadow is not None
            else None
        ),
        routing_version=(
            shadow.routing_version
            if shadow is not None
            else None
        ),
        company_policy_version=(
            shadow.company_policy_version
            if shadow is not None
            else None
        ),
        learned_patterns_version=(
            shadow.learned_patterns_version
            if shadow is not None
            else None
        ),
        cover_letter_final=cover_letter or None,
        surfaced_evidence="[]",
        vacancy_snapshot=_vacancy_snapshot(vacancy),
    )
    session.add(snapshot)
    session.flush()
    return snapshot
