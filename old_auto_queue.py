from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical_cover_letter import final_cover_letter, get_or_enqueue_artifact
from app.db import (
    Application,
    CoverLetterArtifact,
    HhVacancyDiscovery,
    SessionLocal,
    Vacancy,
)
from application_notifications import notify_manual_required
from app.decision_snapshot import ensure_decision_snapshot
from app.old_auto_policy import old_auto_eligibility
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
    """Ensure one eligible OLD application row.

    OLD auto-apply is broad, but not full coverage: queueing happens only after
    scoring/semantic eligibility has passed. Existing terminal states are never
    silently retried.
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
        artifact = get_or_enqueue_artifact(
            session,
            vacancy=vacancy,
            account_key="old",
            assessment=None,
        )
        if artifact.status == "policy_skipped":
            artifact.status = "pending"
            artifact.last_error = None

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


def _existing_old_application(
    session: Session,
    vacancy_id: int,
) -> Application | None:
    return session.scalar(
        select(Application)
        .where(
            Application.vacancy_id == vacancy_id,
            Application.account_key == "old",
        )
        .order_by(Application.id.desc())
        .limit(1)
    )


def _suspend_ineligible_old_application(
    session: Session,
    vacancy: Vacancy,
    *,
    reason: str,
) -> bool:
    application = _existing_old_application(session, vacancy.id)
    if application is None:
        return False

    if application.status not in {AUTO_PENDING_STATUS, "approved", "skipped"}:
        return False

    changed = application.status != "skipped"
    application.status = "skipped"

    artifact = session.scalar(
        select(CoverLetterArtifact)
        .where(
            CoverLetterArtifact.vacancy_id == vacancy.id,
            CoverLetterArtifact.account_key == "old",
        )
        .order_by(CoverLetterArtifact.id.desc())
        .limit(1)
    )
    if artifact is not None and artifact.status in {"pending", "error"}:
        artifact.status = "policy_skipped"
        artifact.last_error = f"old_auto_ineligible:{reason}"[:4000]

    return changed


def seed_old_auto_queue() -> dict[str, int]:
    """Queue only scored, semantically relevant OLD recommendation/search rows.

    Discovery stays broad. Auto-apply does not: deterministic semantic vetoes,
    current CLEAN SKIP decisions and legacy score < 80 are excluded before any
    cover-letter generation or HH apply.
    """
    session = SessionLocal()
    stats = {
        "discoveries": 0,
        "eligible": 0,
        "ineligible": 0,
        "created": 0,
        "pending_letter": 0,
        "approved": 0,
        "preserved": 0,
        "suspended": 0,
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

            eligibility = old_auto_eligibility(session, vacancy.id)
            if not eligibility.eligible:
                stats["ineligible"] += 1
                if _suspend_ineligible_old_application(
                    session,
                    vacancy,
                    reason=eligibility.reason,
                ):
                    stats["suspended"] += 1
                continue

            stats["eligible"] += 1
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


def mark_exhausted_old_letters_manual(*, max_attempts: int) -> int:
    """Move irrecoverable latest OLD cover-letter failures to manual work."""
    session = SessionLocal()
    notifications: list[dict] = []
    changed = 0
    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.account_key == "old",
                Application.status == AUTO_PENDING_STATUS,
            )
            .order_by(Application.id.asc())
        ).all()

        for application, vacancy in rows:
            artifact = session.scalar(
                select(CoverLetterArtifact)
                .where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
                .order_by(CoverLetterArtifact.id.desc())
                .limit(1)
            )
            if artifact is None:
                continue
            if (
                artifact.status != "error"
                or int(artifact.generation_attempts or 0)
                < max(1, int(max_attempts))
            ):
                continue

            application.status = "manual_required"
            reason = (
                "Не удалось подготовить vacancy-bound сопроводительное "
                f"после {artifact.generation_attempts} попыток: "
                f"{artifact.last_error or 'unknown error'}"
            )
            notifications.append(
                {
                    "vacancy_title": vacancy.title,
                    "company": vacancy.company,
                    "vacancy_url": vacancy.url,
                    "application_id": application.id,
                    "reason": reason,
                    "application_sent": False,
                    "account_key": "old",
                    "cover_letter": None,
                }
            )
            changed += 1

        if changed:
            session.commit()
    finally:
        session.close()

    for payload in notifications:
        notify_manual_required(**payload)
    return changed

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
