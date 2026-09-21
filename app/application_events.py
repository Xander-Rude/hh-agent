from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import Application, ApplicationEvent, SessionLocal


HUMAN_CAREER_STATES = {
    "human_response",
    "interview_agreed",
    "interview_done",
}

TERMINAL_CAREER_STATES = {
    "rejected",
    "interview_done",
}

WORKFLOW_CAREER_RANK = {
    "unknown": 0,
    "submitted": 10,
    "viewed": 20,
    "workflow_invited": 30,
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


def record_application_event(
    application_id: int,
    event_type: str,
    *,
    source: str = "hh-agent",
    details: object | None = None,
    observed_at: datetime | None = None,
    dedupe_latest: bool = True,
) -> bool:
    """Append an application event, suppressing identical consecutive noise."""
    details_text = _details_text(details)
    session = SessionLocal()
    try:
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
                and latest.source == source
                and latest.details == details_text
            ):
                return False

        session.add(
            ApplicationEvent(
                application_id=application_id,
                event_type=event_type,
                source=source,
                details=details_text,
                observed_at=_stamp(observed_at),
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def career_transition_allowed(current: str, new: str) -> bool:
    current = (current or "unknown").strip() or "unknown"
    new = (new or "unknown").strip() or "unknown"

    if current in HUMAN_CAREER_STATES and new not in HUMAN_CAREER_STATES:
        return False

    if (
        current in TERMINAL_CAREER_STATES
        and new in {"unknown", "submitted", "viewed", "workflow_invited"}
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
) -> bool:
    """Update career state without letting HH workflow labels fake human contact."""
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

    record_application_event(
        application_id,
        f"career_{career_status}",
        source=source,
        details=details,
        observed_at=observed_at,
        dedupe_latest=True,
    )
    return changed
