from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Application


CONFIRMED_APPLICATION_STATUSES = {
    "applied",
    "already_applied",
}


def normalized_account_key(value: str | None) -> str:
    return str(value or "old").strip().lower() or "old"


def application_is_confirmed_submitted(
    application: Application,
) -> bool:
    return (
        str(application.status or "").strip().lower()
        in CONFIRMED_APPLICATION_STATUSES
        or application.applied_at is not None
    )


def confirmed_other_account_application(
    session: Session,
    *,
    vacancy_id: int,
    account_key: str,
) -> Application | None:
    current_key = normalized_account_key(account_key)
    rows = session.scalars(
        select(Application)
        .where(Application.vacancy_id == vacancy_id)
        .order_by(Application.id.desc())
    ).all()

    for application in rows:
        other_key = normalized_account_key(application.account_key)
        if other_key == current_key:
            continue
        if application_is_confirmed_submitted(application):
            return application

    return None
