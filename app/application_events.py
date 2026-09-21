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
        if current in HUMAN_CAREER_STATES and career_status not in HUMAN_CAREER_STATES:
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
