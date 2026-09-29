from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical_cover_letter import final_cover_letter, get_or_enqueue_artifact
from app.db import Application, HhVacancyDiscovery, SessionLocal, Vacancy
from app.decision_snapshot import ensure_decision_snapshot
from hh_accounts import account_resume_id


AUTO_PENDING_STATUS = "auto_pending_letter"
_DISCOVERY_SOURCES = ("recommendation", "search")
_TERMINAL_OR_OWNED_STATUSES = {
    "approved",
    "applying",
    "applied",
    "already_applied",
    "manual_required",
    "apply_error",
    "company_blacklist",
    "clean_guard_blocked",
}
_REQUEUE_STATUSES = {
    "pending",
    "notified",
    "skipped",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_old_auto_application(
    session: Session,
    vacancy: Vacancy,
) -> tuple[Application, bool]:
    """Ensure one OLD application row for a vacancy discovered by OLD.

    The user has explicitly opted OLD into full-coverage mode.  CLEAN remains
    untouched.  Existing terminal/manual states are never silently retried.
    """
    application = session.scalar(
        select(Application)
        .where(
            Application.vacancy_id == vacancy.id,
            Application.account_key == "old",
        )
        .order_by(Application.id.desc())
        .limit(1)
    )

    created = False
    if application is None:
        application = Application(
            vacancy_id=vacancy.id,
            account_key="old",
            status=AUTO_PENDING_STATUS,
            selected_resume_key="hh-old",
            selected_resume_id=account_resume_id("old"),
        )
        session.add(application)
        session.flush()
        created = True
    elif application.status in _REQUEUE_STATUSES:
        application.status = AUTO_PENDING_STATUS
        application.selected_resume_key = "hh-old"
        application.selected_resume_id = account_resume_id("old")
        application.selected_resume_score = None
    elif application.status not in _TERMINAL_OR_OWNED_STATUSES:
        application.status = AUTO_PENDING_STATUS

    if application.status == AUTO_PENDING_STATUS:
        resume_id = account_resume_id("old")
        if resume_id:
            application.selected_resume_key = "hh-old"
            application.selected_resume_id = resume_id
            application.selected_resume_score = None
        get_or_enqueue_artifact(
            session,
            vacancy=vacancy,
            account_key="old",
            assessment=None,
        )

    return application, created


def promote_old_application_if_ready(
    session: Session,
    vacancy: Vacancy,
) -> Application | None:
    application = session.scalar(
        select(Application)
        .where(
            Application.vacancy_id == vacancy.id,
            Application.account_key == "old",
            Application.status == AUTO_PENDING_STATUS,
        )
        .order_by(Application.id.desc())
        .limit(1)
    )
    if application is None:
        return None

    letter = final_cover_letter(
        session,
        vacancy=vacancy,
        account_key="old",
        assessment=None,
    )
    if not letter:
        return None

    application.cover_letter = letter
    resume_id = account_resume_id("old")
    if resume_id:
        application.selected_resume_key = "hh-old"
        application.selected_resume_id = resume_id
        application.selected_resume_score = None

    ensure_decision_snapshot(
        session,
        application=application,
        vacancy=vacancy,
        approved_cover_letter_override=letter,
    )
    application.status = "approved"
    return application


def seed_old_auto_queue() -> dict[str, int]:
    """Idempotently queue OLD recommendation/search discoveries only.

    Legacy backfilled discoveries are intentionally excluded: they may contain
    historical HH vacancies that are not part of the user's current OLD feeds.
    """
    session = SessionLocal()
    stats = {
        "discoveries": 0,
        "created": 0,
        "pending_letter": 0,
        "approved": 0,
        "preserved": 0,
    }
    try:
        vacancy_ids = list(
            session.scalars(
                select(HhVacancyDiscovery.vacancy_id)
                .where(
                    HhVacancyDiscovery.account_key == "old",
                    HhVacancyDiscovery.discovery_source.in_(_DISCOVERY_SOURCES),
                )
                .distinct()
            )
        )
        stats["discoveries"] = len(vacancy_ids)

        for vacancy_id in vacancy_ids:
            vacancy = session.get(Vacancy, vacancy_id)
            if vacancy is None:
                continue
            application, created = ensure_old_auto_application(session, vacancy)
            if created:
                stats["created"] += 1

            if application.status == AUTO_PENDING_STATUS:
                promoted = promote_old_application_if_ready(
                    session,
                    vacancy,
                )
                if promoted is not None:
                    stats["approved"] += 1
                else:
                    stats["pending_letter"] += 1
            else:
                stats["preserved"] += 1

        session.commit()
        return stats
    finally:
        session.close()


def promote_ready_old_applications(*, limit: int = 500) -> int:
    session = SessionLocal()
    promoted = 0
    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.account_key == "old",
                Application.status == AUTO_PENDING_STATUS,
            )
            .order_by(Application.created_at.asc(), Application.id.asc())
            .limit(max(1, int(limit)))
        ).all()

        for application, vacancy in rows:
            ready = promote_old_application_if_ready(session, vacancy)
            if ready is not None:
                promoted += 1

        if promoted:
            session.commit()
        return promoted
    finally:
        session.close()


def main() -> int:
    stats = seed_old_auto_queue()
    promoted = promote_ready_old_applications()
    print(
        "[OLD AUTO QUEUE] "
        + " ".join(f"{key}={value}" for key, value in stats.items())
        + f" promoted_ready={promoted}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
