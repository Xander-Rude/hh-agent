from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.canonical_cover_letter import generate_artifact
from app.db import CoverLetterArtifact, SessionLocal, init_db
from old_auto_queue import promote_ready_old_applications


MAX_ITEMS = max(
    1,
    int(os.getenv("COVER_LETTER_WORKER_MAX_ITEMS", "40")),
)
MAX_ATTEMPTS = max(
    1,
    int(os.getenv("COVER_LETTER_WORKER_MAX_ATTEMPTS", "2")),
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
                .limit(MAX_ITEMS)
            )
        )
        rows.extend(clean_rows)

        remaining = MAX_ITEMS - len(rows)
        if remaining > 0:
            # Keep a small fresh lane so newly discovered OLD vacancies do not
            # sit behind the initial full-coverage backlog for days.
            fresh_budget = min(10, remaining)
            fresh_old = list(
                session.scalars(
                    select(CoverLetterArtifact)
                    .where(
                        *base_filters,
                        CoverLetterArtifact.account_key == "old",
                    )
                    .order_by(
                        CoverLetterArtifact.created_at.desc(),
                        CoverLetterArtifact.id.desc(),
                    )
                    .limit(fresh_budget)
                )
            )
            rows.extend(fresh_old)
            remaining = MAX_ITEMS - len(rows)

        if remaining > 0:
            selected_ids = [item.id for item in rows]
            query = (
                select(CoverLetterArtifact)
                .where(*base_filters)
                .order_by(
                    CoverLetterArtifact.created_at.asc(),
                    CoverLetterArtifact.id.asc(),
                )
                .limit(remaining)
            )
            if selected_ids:
                query = query.where(
                    ~CoverLetterArtifact.id.in_(selected_ids)
                )
            rows.extend(list(session.scalars(query)))

        print(
            f"[COVER WORKER] queued={len(rows)} "
            f"clean={sum(1 for item in rows if item.account_key == 'clean')} "
            f"old={sum(1 for item in rows if item.account_key == 'old')}"
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
            except Exception as exc:
                failed += 1
                print(
                    "[COVER WORKER] ERROR "
                    f"artifact={artifact.id} vacancy={artifact.vacancy_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
    finally:
        session.close()

    promoted = promote_ready_old_applications(limit=max(100, MAX_ITEMS * 4))
    print(
        f"[COVER WORKER] DONE processed={processed} failed={failed} "
        f"old_promoted={promoted}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
