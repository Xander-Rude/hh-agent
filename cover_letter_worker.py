from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.canonical_cover_letter import generate_artifact
from app.db import CoverLetterArtifact, SessionLocal, Vacancy, init_db
from old_auto_queue import (
    recover_exhausted_old_letters,
    promote_old_application_if_ready,
    promote_ready_old_applications,
    select_old_letter_artifacts,
)


CLEAN_MAX_ITEMS = max(
    1,
    int(os.getenv("COVER_LETTER_WORKER_MAX_ITEMS", "40")),
)
OLD_MAX_ITEMS = max(
    1,
    int(os.getenv("COVER_LETTER_OLD_MAX_ITEMS", "500")),
)
MAX_ATTEMPTS = max(
    1,
    int(os.getenv("COVER_LETTER_WORKER_MAX_ATTEMPTS", "2")),
)
OLD_FRESH_BUDGET = max(
    0,
    int(os.getenv("COVER_LETTER_OLD_FRESH_BUDGET", "20")),
)
OLD_RETRY_BUDGET = max(
    0,
    int(os.getenv("COVER_LETTER_OLD_RETRY_BUDGET", "5")),
)
OLD_SLA_MINUTES = max(
    1,
    int(os.getenv("COVER_LETTER_OLD_SLA_MINUTES", "30")),
)


def main() -> int:
    init_db()
    session = SessionLocal()
    stale_before = (
        datetime.now(UTC).replace(tzinfo=None)
        - timedelta(minutes=15)
    )
    stale_rows = list(
        session.scalars(
            select(CoverLetterArtifact).where(
                CoverLetterArtifact.status == "generating",
                CoverLetterArtifact.updated_at < stale_before,
            )
        )
    )
    for artifact in stale_rows:
        artifact.status = "error"
        artifact.last_error = "stale_generation_recovered"
    if stale_rows:
        session.commit()
        print(f"[COVER WORKER] recovered_stale={len(stale_rows)}")

    processed = 0
    failed = 0
    promoted_inline = 0
    old_stats = {
        "auto_pending": 0,
        "eligible": 0,
        "ineligible": 0,
        "actionable": 0,
        "overdue": 0,
        "superseded": 0,
        "missing_artifact": 0,
        "oldest_wait_min": 0,
    }
    try:
        base_filters = (
            CoverLetterArtifact.status.in_(("pending", "error")),
            CoverLetterArtifact.generation_attempts < MAX_ATTEMPTS,
        )
        rows: list[CoverLetterArtifact] = []
        clean_rows = list(
            session.scalars(
                select(CoverLetterArtifact)
                .where(
                    *base_filters,
                    CoverLetterArtifact.account_key == "clean",
                )
                .order_by(
                    CoverLetterArtifact.created_at.asc(),
                    CoverLetterArtifact.id.asc(),
                )
                .limit(CLEAN_MAX_ITEMS)
            )
        )
        rows.extend(clean_rows)

        old_rows, old_stats = select_old_letter_artifacts(
            session,
            limit=OLD_MAX_ITEMS,
            max_attempts=MAX_ATTEMPTS,
            fresh_budget=OLD_FRESH_BUDGET,
            retry_budget=OLD_RETRY_BUDGET,
            sla_minutes=OLD_SLA_MINUTES,
        )
        rows.extend(old_rows)
        if old_stats["superseded"]:
            session.commit()

        print(
            f"[COVER WORKER] queued={len(rows)} "
            f"clean={sum(1 for item in rows if item.account_key == 'clean')} "
            f"old={sum(1 for item in rows if item.account_key == 'old')} "
            f"old_eligible={old_stats['eligible']} "
            f"old_actionable={old_stats['actionable']} "
            f"old_overdue={old_stats['overdue']} "
            f"old_oldest_wait_min={old_stats['oldest_wait_min']} "
            f"old_superseded={old_stats['superseded']}"
        )
        for artifact in rows:
            try:
                generate_artifact(session, artifact)
                processed += 1
                print(
                    "[COVER WORKER] final "
                    f"artifact={artifact.id} vacancy={artifact.vacancy_id} "
                    f"attempts={artifact.generation_attempts}"
                )

                if artifact.account_key == "old":
                    vacancy = session.get(Vacancy, artifact.vacancy_id)
                    if (
                        vacancy is not None
                        and promote_old_application_if_ready(session, vacancy)
                        is not None
                    ):
                        session.commit()
                        promoted_inline += 1
                        print(
                            "[COVER WORKER] old promoted inline "
                            f"vacancy={artifact.vacancy_id}"
                        )
            except Exception as exc:
                failed += 1
                print(
                    "[COVER WORKER] ERROR "
                    f"artifact={artifact.id} vacancy={artifact.vacancy_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
    finally:
        session.close()

    promoted_sweep = promote_ready_old_applications(
        limit=max(100, OLD_MAX_ITEMS)
    )
    recovered_fallback = recover_exhausted_old_letters(
        max_attempts=MAX_ATTEMPTS,
    )
    print(
        f"[COVER WORKER] DONE processed={processed} failed={failed} "
        f"old_promoted_inline={promoted_inline} "
        f"old_promoted_sweep={promoted_sweep} "
        f"old_fallback={recovered_fallback}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
