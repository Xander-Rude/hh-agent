from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical_cover_letter import (
    RESUME_PATH,
    cover_letter_guard_issues,
    final_cover_letter,
    get_or_enqueue_artifact,
)
from app.db import (
    Application,
    CoverLetterArtifact,
    HhVacancyDiscovery,
    SessionLocal,
    Vacancy,
)
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


def _old_fallback_cover_letter(vacancy: Vacancy) -> str:
    """Build a safe OLD-only fallback when LLM letter generation is exhausted."""
    sample = f"{vacancy.title or ''}\n{vacancy.description or ''}"
    cyr = len(__import__("re").findall(r"[А-Яа-яЁё]", sample))
    lat = len(__import__("re").findall(r"[A-Za-z]", sample))
    if lat > cyr:
        text = (
            "Hello!\n\n"
            "I manage end-to-end IT projects from requirements and planning "
            "through development coordination, acceptance and production launch. "
            "My work connects business stakeholders with engineering, analytics, "
            "QA, architecture and DevOps while keeping timelines, risks, "
            "dependencies and changes visible.\n\n"
            "I am most effective where delivery needs clear coordination, "
            "transparent decisions and consistent follow-through to a concrete "
            "production result. I keep agreements documented and stay involved "
            "throughout the full project lifecycle.\n\n"
            "Best regards,\nAleksandr Rudenko"
        )
    else:
        text = (
            "Здравствуйте!\n\n"
            "Я управляю IT-проектами полного цикла: от формализации требований "
            "и планирования до координации разработки, приемки и запуска в "
            "production. В работе связываю бизнес, аналитику, разработку, QA, "
            "архитектуру и DevOps, держу прозрачными сроки, риски, зависимости "
            "и изменения.\n\n"
            "Мне особенно близки задачи, где нужно выстроить управляемый delivery, "
            "синхронизировать участников и сохранять понятный след решений в "
            "документации. Привык доводить изменения до фактического результата "
            "и работать со стейкхолдерами на протяжении всего цикла проекта.\n\n"
            "С уважением,\nАлександр Руденко"
        )

    resume = (
        RESUME_PATH.read_text(encoding="utf-8", errors="replace")
        if RESUME_PATH.exists()
        else ""
    )
    issues = cover_letter_guard_issues(
        text,
        vacancy_title=vacancy.title or "",
        vacancy_company=vacancy.company,
        resume_text=resume,
        extraction_json=None,
    )
    if issues:
        raise RuntimeError(
            "OLD deterministic cover-letter fallback failed guard: "
            + ",".join(issues)
        )
    return text


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


def select_old_letter_artifacts(
    session: Session,
    *,
    limit: int,
    max_attempts: int,
    fresh_budget: int = 20,
    retry_budget: int = 5,
    sla_minutes: int = 30,
) -> tuple[list[CoverLetterArtifact], dict[str, int]]:
    """Select actionable OLD cover-letter work without backlog starvation.

    Only applications that are still auto-pending and pass the current OLD
    score + semantic gate can consume LLM capacity. For each vacancy only the
    latest artifact is actionable; older pending/error artifacts are retired.
    """
    limit = max(0, int(limit))
    max_attempts = max(1, int(max_attempts))
    fresh_budget = max(0, int(fresh_budget))
    retry_budget = max(0, int(retry_budget))
    sla_minutes = max(1, int(sla_minutes))
    now = _utcnow()
    overdue_before = now - timedelta(minutes=sla_minutes)

    rows = session.execute(
        select(Application, Vacancy)
        .join(Vacancy, Vacancy.id == Application.vacancy_id)
        .where(
            Application.account_key == "old",
            Application.status == AUTO_PENDING_STATUS,
        )
        .order_by(Application.created_at.asc(), Application.id.asc())
    ).all()

    candidates: list[tuple[Application, CoverLetterArtifact]] = []
    stats = {
        "auto_pending": len(rows),
        "eligible": 0,
        "ineligible": 0,
        "actionable": 0,
        "overdue": 0,
        "superseded": 0,
        "missing_artifact": 0,
        "oldest_wait_min": 0,
    }

    for application, vacancy in rows:
        eligibility = old_auto_eligibility(session, vacancy.id)
        if not eligibility.eligible:
            stats["ineligible"] += 1
            continue
        stats["eligible"] += 1

        artifacts = list(
            session.scalars(
                select(CoverLetterArtifact)
                .where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
                .order_by(CoverLetterArtifact.id.desc())
            )
        )
        if not artifacts:
            stats["missing_artifact"] += 1
            continue

        latest = artifacts[0]
        for stale in artifacts[1:]:
            if stale.status not in {"pending", "error"}:
                continue
            stale.status = "policy_skipped"
            stale.last_error = f"superseded_by_artifact:{latest.id}"
            stats["superseded"] += 1

        if latest.status not in {"pending", "error"}:
            continue
        if int(latest.generation_attempts or 0) >= max_attempts:
            continue

        created_at = application.created_at or now
        wait_minutes = max(
            0,
            int((now - created_at).total_seconds() // 60),
        )
        stats["oldest_wait_min"] = max(
            stats["oldest_wait_min"],
            wait_minutes,
        )
        if created_at <= overdue_before:
            stats["overdue"] += 1
        candidates.append((application, latest))

    stats["actionable"] = len(candidates)
    if limit <= 0 or not candidates:
        return [], stats

    selected: list[tuple[Application, CoverLetterArtifact]] = []
    selected_ids: set[int] = set()

    def take(
        source: list[tuple[Application, CoverLetterArtifact]],
        budget: int,
    ) -> None:
        for application, artifact in source:
            if len(selected) >= limit or budget <= 0:
                break
            if artifact.id in selected_ids:
                continue
            selected.append((application, artifact))
            selected_ids.add(artifact.id)
            budget -= 1

    retries = sorted(
        (
            item
            for item in candidates
            if item[1].status == "error"
        ),
        key=lambda item: (
            item[0].created_at or now,
            item[0].id,
        ),
    )
    take(retries, min(retry_budget, limit))

    # Reserve a recent lane regardless of SLA age. A worker can be delayed
    # for hours by another pipeline; if every candidate becomes "overdue",
    # filtering the recent lane by SLA recreates a middle-of-queue starvation
    # band. Deduplication in take() lets retries win without double selection.
    recent = sorted(
        candidates,
        key=lambda item: (
            item[0].created_at or now,
            item[0].id,
        ),
        reverse=True,
    )
    take(recent, min(fresh_budget, max(0, limit - len(selected))))

    overdue = sorted(
        (
            item
            for item in candidates
            if (item[0].created_at or now) <= overdue_before
        ),
        key=lambda item: (
            item[0].created_at or now,
            item[0].id,
        ),
    )
    take(overdue, max(0, limit - len(selected)))

    remaining = sorted(
        candidates,
        key=lambda item: (
            item[0].created_at or now,
            item[0].id,
        ),
    )
    take(remaining, max(0, limit - len(selected)))

    return [artifact for _, artifact in selected], stats


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


def recover_exhausted_old_letters(*, max_attempts: int) -> int:
    """Recover exhausted OLD letter generation without creating manual work."""
    session = SessionLocal()
    recovered = 0
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

            eligibility = old_auto_eligibility(session, vacancy.id)
            if not eligibility.eligible:
                continue

            fallback = _old_fallback_cover_letter(vacancy)
            artifact.final_text = fallback
            if not (artifact.draft_text or "").strip():
                artifact.draft_text = fallback
            artifact.status = "final"
            artifact.validation_json = '["deterministic_old_fallback"]'
            artifact.last_error = None
            artifact.finalized_at = _utcnow()
            artifact.updated_at = _utcnow()

            promoted = promote_old_application_if_ready(session, vacancy)
            if promoted is not None:
                recovered += 1

        if recovered:
            session.commit()
        return recovered
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
