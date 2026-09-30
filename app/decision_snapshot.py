from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cover_letter_runtime import (
    build_legacy_vacancy_cover_letter,
    is_vacancy_bound_cover_letter,
    parse_strengths,
)
from app.clean_live_guard import current_clean_assessment
from app.canonical_cover_letter import (
    RESUME_PATH,
    cover_letter_guard_issues,
    get_or_generate_cover_letter,
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


def _final_cover_letter(
    *,
    application: Application,
    vacancy: Vacancy,
    evaluation: Evaluation | None,
    shadow: CleanShadowAssessment | None,
    approved_cover_letter_override: str | None = None,
) -> str:
    account_key = application.account_key or "old"
    vacancy_source = (vacancy.source or "hh").strip().lower()
    current = (application.cover_letter or "").strip()

    if approved_cover_letter_override is not None:
        override = approved_cover_letter_override.strip()
        if not override:
            raise ValueError("approved cover-letter override is empty")
        resume_text = (
            RESUME_PATH.read_text(encoding="utf-8", errors="replace")
            if RESUME_PATH.exists()
            else ""
        )
        issues = cover_letter_guard_issues(
            override,
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            resume_text=resume_text,
            extraction_json=(shadow.extraction_json if shadow is not None else None),
        )
        if issues:
            raise ValueError(
                "refusing to snapshot invalid approved cover-letter override: "
                + ",".join(issues)
            )
        return override

    if account_key == "clean" and vacancy_source == "hh":
        if not current:
            raise ValueError(
                "refusing to approve CLEAN application without canonical cover letter"
            )
        resume_text = (
            RESUME_PATH.read_text(encoding="utf-8", errors="replace")
            if RESUME_PATH.exists()
            else ""
        )
        issues = cover_letter_guard_issues(
            current,
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            resume_text=resume_text,
            extraction_json=(shadow.extraction_json if shadow is not None else None),
        )
        if issues:
            raise ValueError(
                "refusing to snapshot invalid canonical cover letter: "
                + ",".join(issues)
            )
        return current

    if evaluation is not None:
        result = build_legacy_vacancy_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            stored_text=evaluation.cover_letter,
            strengths=parse_strengths(evaluation.strengths),
        ).strip()
    elif current:
        result = build_legacy_vacancy_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            stored_text=current,
            strengths=[],
        ).strip()
    else:
        return ""

    if not is_vacancy_bound_cover_letter(
        result,
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
    ):
        raise ValueError(
            "refusing to snapshot a cover letter that is not bound "
            f"to vacancy_id={vacancy.id}"
        )
    return result


def ensure_decision_snapshot(
    session: Session,
    *,
    application: Application,
    vacancy: Vacancy,
    approved_cover_letter_override: str | None = None,
) -> ApplicationDecisionSnapshot:
    """Persist the exact decision state once, at approval time.

    The snapshot is intentionally idempotent and immutable. It captures the
    latest visible legacy evaluation plus the current-version CLEAN assessment
    for CLEAN HH applications. OLD/external applications keep the latest shadow
    only as descriptive context.
    """
    existing = get_decision_snapshot(
        session,
        application.id,
    )
    if existing is not None:
        return existing

    account_key = application.account_key or "old"
    vacancy_source = (vacancy.source or "hh").strip().lower()

    evaluation = _latest_evaluation(
        session,
        vacancy.id,
    )
    if account_key == "clean" and vacancy_source == "hh":
        shadow = current_clean_assessment(
            session,
            vacancy.id,
        )
    else:
        shadow = _latest_shadow(
            session,
            vacancy.id,
        )

    cover_letter = _final_cover_letter(
        application=application,
        vacancy=vacancy,
        evaluation=evaluation,
        shadow=shadow,
        approved_cover_letter_override=approved_cover_letter_override,
    )
    application.cover_letter = cover_letter or None

    if evaluation is not None:
        application.selected_resume_key = evaluation.selected_resume_key
        application.selected_resume_title = evaluation.selected_resume_title
        application.selected_resume_id = evaluation.selected_resume_id
        application.selected_resume_score = evaluation.selected_resume_score

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


def refresh_pending_decision_snapshot_cover_letter(
    session: Session,
    *,
    application: Application,
    vacancy: Vacancy,
) -> ApplicationDecisionSnapshot | None:
    """Replace only a stale pre-send cover-letter snapshot.

    Historical snapshots stay immutable: a corrected row is appended and
    becomes the latest snapshot. Already submitted applications are never
    rewritten.
    """
    existing = get_decision_snapshot(session, application.id)
    if existing is None:
        return None

    if application.applied_at is not None or application.status == "applied":
        return existing

    current = (existing.cover_letter_final or "").strip()
    account_key = application.account_key or "old"
    vacancy_source = (vacancy.source or "hh").strip().lower()

    if account_key == "old" and vacancy_source == "hh":
        repaired, _ = get_or_generate_cover_letter(
            session,
            vacancy=vacancy,
            account_key="old",
            assessment=None,
        )
    else:
        if current and is_vacancy_bound_cover_letter(
            current,
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
        ):
            return existing

        evaluation = (
            session.get(Evaluation, existing.legacy_evaluation_id)
            if existing.legacy_evaluation_id is not None
            else _latest_evaluation(session, vacancy.id)
        )
        shadow = (
            session.get(CleanShadowAssessment, existing.shadow_assessment_id)
            if existing.shadow_assessment_id is not None
            else None
        )

        if (
            shadow is None
            and account_key == "clean"
            and vacancy_source == "hh"
        ):
            shadow = current_clean_assessment(session, vacancy.id)

        repaired = _final_cover_letter(
            application=application,
            vacancy=vacancy,
            evaluation=evaluation,
            shadow=shadow,
        )
    if not repaired or repaired == current:
        return existing

    values = {
        column.name: getattr(existing, column.name)
        for column in ApplicationDecisionSnapshot.__table__.columns
        if column.name != "id"
    }
    values["cover_letter_final"] = repaired
    values["vacancy_snapshot"] = _vacancy_snapshot(vacancy)

    replacement = ApplicationDecisionSnapshot(**values)
    session.add(replacement)
    application.cover_letter = repaired
    session.flush()
    return replacement
