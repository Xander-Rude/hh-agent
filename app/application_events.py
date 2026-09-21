from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db import Application, ApplicationEvent, SessionLocal


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _details_json(details: dict[str, Any] | None) -> str | None:
    if not details:
        return None
    return json.dumps(details, ensure_ascii=False, sort_keys=True)


def record_application_event(
    application_id: int,
    event_type: str,
    *,
    source: str,
    details: dict[str, Any] | None = None,
    is_human_contact: bool = False,
    dedupe_key: str | None = None,
    observed_at: datetime | None = None,
) -> bool:
    """Persist one immutable application event.

    dedupe_key is optional. When present it makes polling workers idempotent,
    while technical status transitions can still be stored every time they
    genuinely change.
    """
    session = SessionLocal()
    try:
        event = ApplicationEvent(
            application_id=application_id,
            event_type=event_type,
            source=source,
            details=_details_json(details),
            is_human_contact=is_human_contact,
            dedupe_key=dedupe_key,
            observed_at=observed_at or _utcnow_naive(),
        )
        session.add(event)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        return True
    finally:
        session.close()


def set_career_state(
    application_id: int,
    state: str,
    *,
    source: str,
    details: dict[str, Any] | None = None,
    is_human_contact: bool = False,
    dedupe_key: str | None = None,
) -> bool:
    """Update the current career funnel state and append an immutable event."""
    session = SessionLocal()
    changed = False
    try:
        application = session.get(Application, application_id)
        if application is None:
            return False

        previous = application.career_state
        now = _utcnow_naive()
        application.response_checked_at = now

        if previous != state:
            application.career_state = state
            application.career_state_updated_at = now
            changed = True

        if is_human_contact and application.human_response_at is None:
            application.human_response_at = now

        session.commit()
    finally:
        session.close()

    if changed:
        record_application_event(
            application_id,
            f"career_state:{state}",
            source=source,
            details={
                "previous": previous,
                "current": state,
                **(details or {}),
            },
            is_human_contact=is_human_contact,
            dedupe_key=dedupe_key,
        )
    return changed


def touch_response_check(application_id: int) -> None:
    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return
        application.response_checked_at = _utcnow_naive()
        session.commit()
    finally:
        session.close()
