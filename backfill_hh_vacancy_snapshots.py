from __future__ import annotations

import argparse
import time

from sqlalchemy import or_, select

from app.db import SessionLocal, Vacancy
from app.hh_vacancy_snapshot import (
    build_hh_source_payload,
    fetch_hh_vacancy_api_payload,
    record_vacancy_source_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill full HH vacancy API payloads and key skills for "
            "vacancies already stored in hh_agent.db."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum vacancies per run; 0 means no limit (default: 200).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Delay between HH API requests in seconds (default: 0.5).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Attempts per vacancy before moving on (default: 3).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh vacancies that already have a source payload.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = SessionLocal()

    try:
        stmt = (
            select(Vacancy)
            .where(
                or_(
                    Vacancy.source == "hh",
                    Vacancy.source.is_(None),
                )
            )
            .order_by(Vacancy.id.desc())
        )

        if not args.force:
            stmt = stmt.where(
                or_(
                    Vacancy.source_payload_hash.is_(None),
                    Vacancy.source_payload_hash == "",
                )
            )

        if args.limit > 0:
            stmt = stmt.limit(args.limit)

        vacancies = list(session.scalars(stmt).all())
        print(
            "[HH SNAPSHOT BACKFILL] "
            f"selected={len(vacancies)} force={int(args.force)}"
        )

        ok = 0
        failed = 0
        changed = 0

        for index, vacancy in enumerate(vacancies, start=1):
            hh_id = str(vacancy.external_id or vacancy.hh_id or "").strip()

            if not hh_id.isdigit():
                print(
                    "[HH SNAPSHOT BACKFILL SKIP] "
                    f"vacancy={vacancy.id} invalid_hh_id={hh_id!r}"
                )
                failed += 1
                continue

            payload = None
            last_error: Exception | None = None

            for attempt in range(1, max(1, args.retries) + 1):
                try:
                    payload = fetch_hh_vacancy_api_payload(hh_id)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max(1, args.retries):
                        time.sleep(max(1.0, args.sleep * 2))

            if payload is None:
                failed += 1
                print(
                    "[HH SNAPSHOT BACKFILL ERROR] "
                    f"{index}/{len(vacancies)} vacancy={vacancy.id} "
                    f"hh={hh_id} error={type(last_error).__name__}: "
                    f"{last_error}"
                )
                session.rollback()
                time.sleep(max(0.0, args.sleep))
                continue

            source_payload = build_hh_source_payload(
                hh_id=hh_id,
                api_payload=payload,
                dom_snapshot={},
            )

            try:
                added = record_vacancy_source_snapshot(
                    session,
                    vacancy,
                    source_payload,
                )
                session.commit()
                ok += 1
                changed += int(added)
                print(
                    "[HH SNAPSHOT BACKFILL OK] "
                    f"{index}/{len(vacancies)} vacancy={vacancy.id} "
                    f"hh={hh_id} skills={len(payload.get('key_skills') or [])} "
                    f"history_added={int(added)}"
                )
            except Exception as exc:
                session.rollback()
                failed += 1
                print(
                    "[HH SNAPSHOT BACKFILL DB ERROR] "
                    f"{index}/{len(vacancies)} vacancy={vacancy.id} "
                    f"hh={hh_id} error={type(exc).__name__}: {exc}"
                )

            time.sleep(max(0.0, args.sleep))

        print(
            "[HH SNAPSHOT BACKFILL DONE] "
            f"ok={ok} failed={failed} history_added={changed}"
        )
        return 0 if failed == 0 else 2

    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
