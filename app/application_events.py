from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    SessionLocal,
    Vacancy,
)


OUTCOME_EVENT_CLASSES = {
    "card_notified": "technical",
    "approved": "decision",
    "apply_started": "technical",
    "applied": "technical",
    "already_applied": "technical",
    "manual_required": "technical",
    "manual_applied_confirmed": "technical",
    "viewed": "platform_outcome",
    "workflow_invited": "platform_workflow",
    "workflow_discarded": "platform_workflow",
    "rejected": "human_platform_outcome",
    "recruiter_message": "human_contact",
    "screening_call": "human_stage",
    "interview_scheduled": "human_stage",
    "interview_completed": "human_stage",
    "next_stage": "human_stage",
    "final_stage": "human_stage",
    "offer": "human_stage",
    "declined_by_user": "human_terminal",
    "withdrawn": "terminal",
    "vacancy_closed": "vacancy_terminal",
    "no_response_7d": "derived",
    "no_response_30d": "derived",
}

OUTCOME_ATTRIBUTIONS = {
    "hh_clean",
    "hh_old",
    "career_site",
    "targeted_hunt",
    "assisted_multi_touch",
    "unknown",
}

OUTCOME_CONFIDENCE = {
    "system_confirmed",
    "platform_observed",
    "user_confirmed",
    "trusted_collector",
    "derived",
    "unknown",
}

CAREER_EVENT_BY_STATUS = {
    "submitted": "applied",
    "viewed": "viewed",
    "workflow_invited": "workflow_invited",
    "workflow_discarded": "workflow_discarded",
    "rejected": "rejected",
    # Legacy materialized-state aliases.
    "human_response": "recruiter_message",
    "interview_agreed": "interview_scheduled",
    "interview_done": "interview_completed",
    # Canonical human stages.
    "recruiter_message": "recruiter_message",
    "screening_call": "screening_call",
    "interview_scheduled": "interview_scheduled",
    "interview_completed": "interview_completed",
    "next_stage": "next_stage",
    "final_stage": "final_stage",
    "offer": "offer",
    "declined_by_user": "declined_by_user",
    "withdrawn": "withdrawn",
    "vacancy_closed": "vacancy_closed",
}

HUMAN_CAREER_STATES = {
    "human_response",
    "interview_agreed",
    "interview_done",
    "recruiter_message",
    "screening_call",
    "interview_scheduled",
    "interview_completed",
    "next_stage",
    "final_stage",
    "offer",
    "declined_by_user",
    "withdrawn",
}

TERMINAL_CAREER_STATES = {
    "rejected",
    "declined_by_user",
    "withdrawn",
    "vacancy_closed",
}

PLATFORM_CAREER_STATES = {
    "unknown",
    "submitted",
    "viewed",
    "workflow_invited",
    "workflow_discarded",
}

HUMAN_CAREER_RANK = {
    "human_response": 10,
    "recruiter_message": 10,
    "screening_call": 20,
    "interview_agreed": 30,
    "interview_scheduled": 30,
    "interview_done": 40,
    "interview_completed": 40,
    "next_stage": 50,
    "final_stage": 60,
    "offer": 70,
    "declined_by_user": 100,
    "withdrawn": 100,
}

WORKFLOW_CAREER_RANK = {
    "unknown": 0,
    "submitted": 10,
    "viewed": 20,
    "workflow_invited": 30,
    "workflow_discarded": 40,
    "rejected": 100,
}


def _stamp(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _details_text(details: object | None) -> str | None:
    if details is None:
        return None
    if isinstance(details, str):
        return details
    return json.dumps(details, ensure_ascii=False, sort_keys=True)


def _latest_snapshot(
    session,
    application_id: int,
) -> ApplicationDecisionSnapshot | None:
    return session.scalar(
        select(ApplicationDecisionSnapshot)
        .where(
            ApplicationDecisionSnapshot.application_id == application_id
        )
        .order_by(ApplicationDecisionSnapshot.id.desc())
        .limit(1)
    )


def _derive_attribution(
    session,
    application: Application,
    snapshot: ApplicationDecisionSnapshot | None,
) -> str:
    vacancy = session.get(Vacancy, application.vacancy_id)
    source = (
        (vacancy.source or "").strip().lower()
        if vacancy is not None
        else ""
    )

    if source and source != "hh":
        return "career_site"

    account_key = (
        snapshot.account_key
        if snapshot is not None
        else (application.account_key or "old")
    )
    if account_key == "clean":
        return "hh_clean"
    if account_key == "old":
        return "hh_old"
    return "unknown"


def _record_event(
    *,
    application_id: int,
    event_type: str,
    event_class: str | None,
    source: str,
    details: object | None,
    observed_at: datetime | None,
    dedupe_latest: bool,
    attribution: str | None,
    confidence: str,
    raw_ref: str | None,
    decision_snapshot_id: int | None,
) -> bool:
    details_text = _details_text(details)
    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return False

        snapshot = None
        if decision_snapshot_id is not None:
            snapshot = session.get(
                ApplicationDecisionSnapshot,
                decision_snapshot_id,
            )
            if (
                snapshot is None
                or snapshot.application_id != application_id
            ):
                raise ValueError(
                    "decision_snapshot_id does not belong to application"
                )
        else:
            snapshot = _latest_snapshot(session, application_id)
            if snapshot is not None:
                decision_snapshot_id = snapshot.id

        resolved_attribution = (
            attribution
            or _derive_attribution(session, application, snapshot)
        )
        if resolved_attribution not in OUTCOME_ATTRIBUTIONS:
            raise ValueError(
                f"Unsupported outcome attribution: {resolved_attribution}"
            )
        if confidence not in OUTCOME_CONFIDENCE:
            raise ValueError(
                f"Unsupported outcome confidence: {confidence}"
            )

        if dedupe_latest:
            latest = session.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == application_id)
                .order_by(
                    ApplicationEvent.observed_at.desc(),
                    ApplicationEvent.id.desc(),
                )
                .limit(1)
            ).first()
            if (
                latest is not None
                and latest.event_type == event_type
                and latest.event_class == event_class
                and latest.source == source
                and latest.attribution == resolved_attribution
                and latest.confidence == confidence
                and latest.raw_ref == raw_ref
                and latest.details == details_text
                and latest.decision_snapshot_id == decision_snapshot_id
            ):
                return False

        session.add(
            ApplicationEvent(
                application_id=application_id,
                decision_snapshot_id=decision_snapshot_id,
                event_type=event_type,
                event_class=event_class,
                source=source,
                attribution=resolved_attribution,
                confidence=confidence,
                raw_ref=raw_ref,
                details=details_text,
                observed_at=_stamp(observed_at),
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def record_application_event(
    application_id: int,
    event_type: str,
    *,
    source: str = "hh-agent",
    details: object | None = None,
    observed_at: datetime | None = None,
    dedupe_latest: bool = True,
    attribution: str | None = None,
    confidence: str = "unknown",
    raw_ref: str | None = None,
    decision_snapshot_id: int | None = None,
) -> bool:
    """Backward-compatible generic append-only event writer."""
    return _record_event(
        application_id=application_id,
        event_type=event_type,
        event_class=OUTCOME_EVENT_CLASSES.get(event_type),
        source=source,
        details=details,
        observed_at=observed_at,
        dedupe_latest=dedupe_latest,
        attribution=attribution,
        confidence=confidence,
        raw_ref=raw_ref,
        decision_snapshot_id=decision_snapshot_id,
    )


def record_outcome_event(
    application_id: int,
    event_type: str,
    *,
    source: str,
    details: object | None = None,
    observed_at: datetime | None = None,
    dedupe_latest: bool = True,
    attribution: str | None = None,
    confidence: str = "unknown",
    raw_ref: str | None = None,
    decision_snapshot_id: int | None = None,
) -> bool:
    """Append a canonical outcome event with immutable provenance."""
    event_class = OUTCOME_EVENT_CLASSES.get(event_type)
    if event_class is None:
        raise ValueError(f"Unsupported outcome event: {event_type}")

    return _record_event(
        application_id=application_id,
        event_type=event_type,
        event_class=event_class,
        source=source,
        details=details,
        observed_at=observed_at,
        dedupe_latest=dedupe_latest,
        attribution=attribution,
        confidence=confidence,
        raw_ref=raw_ref,
        decision_snapshot_id=decision_snapshot_id,
    )


def career_transition_allowed(current: str, new: str) -> bool:
    current = (current or "unknown").strip() or "unknown"
    new = (new or "unknown").strip() or "unknown"

    # Generic platform observations must never erase a human-confirmed
    # stage. Explicit terminal outcomes such as rejected remain valid after
    # an interview.
    if current in HUMAN_CAREER_STATES and new in PLATFORM_CAREER_STATES:
        return False

    if (
        current in HUMAN_CAREER_RANK
        and new in HUMAN_CAREER_RANK
        and HUMAN_CAREER_RANK[new] < HUMAN_CAREER_RANK[current]
    ):
        return False

    if (
        current in TERMINAL_CAREER_STATES
        and new in PLATFORM_CAREER_STATES
    ):
        return False

    if (
        current in WORKFLOW_CAREER_RANK
        and new in WORKFLOW_CAREER_RANK
        and WORKFLOW_CAREER_RANK[new] < WORKFLOW_CAREER_RANK[current]
    ):
        return False

    return True


def update_career_status(
    application_id: int,
    career_status: str,
    *,
    source: str = "hh",
    details: object | None = None,
    observed_at: datetime | None = None,
    attribution: str | None = None,
    confidence: str = "unknown",
    raw_ref: str | None = None,
    emit_event: bool = True,
) -> bool:
    """Materialize career state and append its canonical outcome event."""
    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return False

        current = (application.career_status or "unknown").strip() or "unknown"

        # A later scrape of an HH page may only expose a generic workflow label.
        # Never downgrade a state that was explicitly confirmed as human contact.
        if not career_transition_allowed(current, career_status):
            application.response_checked_at = _stamp(observed_at)
            session.commit()
            return False

        changed = current != career_status
        application.career_status = career_status
        application.response_checked_at = _stamp(observed_at)
        session.commit()
    finally:
        session.close()

    event_type = CAREER_EVENT_BY_STATUS.get(career_status)
    if emit_event and event_type is not None:
        record_outcome_event(
            application_id,
            event_type,
            source=source,
            details=details,
            observed_at=observed_at,
            attribution=attribution,
            confidence=confidence,
            raw_ref=raw_ref,
            dedupe_latest=True,
        )
    return changed
