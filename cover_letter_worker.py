from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.canonical_cover_letter import generate_artifact
from app.db import CoverLetterArtifact, SessionLocal, init_db


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
        rows = list(
            session.scalars(
                select(CoverLetterArtifact)
                .where(
                    CoverLetterArtifact.status.in_(("pending", "error")),
                    CoverLetterArtifact.generation_attempts < MAX_ATTEMPTS,
                )
                .order_by(
                    CoverLetterArtifact.created_at.asc(),
                    CoverLetterArtifact.id.asc(),
                )
                .limit(MAX_ITEMS)
            )
        )
        print(f"[COVER WORKER] queued={len(rows)}")
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

    print(
        f"[COVER WORKER] DONE processed={processed} failed={failed}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
