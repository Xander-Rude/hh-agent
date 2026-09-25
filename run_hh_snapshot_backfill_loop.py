from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from backfill_hh_vacancy_snapshots import notify_telegram


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "hh_agent.db"
BACKFILL_SCRIPT = ROOT / "backfill_hh_vacancy_snapshots.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continuously run HH vacancy snapshot backfill in small batches "
            "until all missing snapshots are filled or a batch fails/blocks."
        )
    )
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--card-sleep", type=float, default=12.0)
    parser.add_argument("--batch-pause", type=float, default=60.0)
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="0 means unlimited until no missing snapshots remain.",
    )
    return parser.parse_args()


def remaining_count() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM vacancies
            WHERE COALESCE(source, 'hh') = 'hh'
              AND (
                source_payload_hash IS NULL
                OR source_payload_hash = ''
              )
            """
        ).fetchone()
    return int(row[0] if row else 0)


def main() -> int:
    args = parse_args()
    batch_size = max(1, args.batch_size)
    card_sleep = max(0.0, args.card_sleep)
    batch_pause = max(0.0, args.batch_pause)
    max_batches = max(0, args.max_batches)

    env = os.environ.copy()
    env["HH_COLLECT_HEADLESS"] = "true"

    batch_no = 0
    while True:
        remaining = remaining_count()
        print(
            "[HH SNAPSHOT LOOP] "
            f"remaining={remaining} completed_batches={batch_no}",
            flush=True,
        )

        if remaining <= 0:
            notify_telegram(
                "HH snapshot backfill завершён. "
                "Для всех доступных строк в очереди полный snapshot собран."
            )
            return 0

        if max_batches and batch_no >= max_batches:
            print(
                "[HH SNAPSHOT LOOP] max_batches reached; stopping cleanly.",
                flush=True,
            )
            return 0

        batch_no += 1
        print(
            "[HH SNAPSHOT LOOP] "
            f"starting batch={batch_no} size={batch_size} "
            f"card_sleep={card_sleep}s",
            flush=True,
        )

        result = subprocess.run(
            [
                sys.executable,
                "-u",
                str(BACKFILL_SCRIPT),
                "--limit",
                str(batch_size),
                "--sleep",
                str(card_sleep),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )

        remaining_after = remaining_count()
        print(
            "[HH SNAPSHOT LOOP] "
            f"batch={batch_no} exit={result.returncode} "
            f"remaining={remaining_after}",
            flush=True,
        )

        if result.returncode == 3:
            # Child already sent the detailed CAPTCHA/anti-bot alert.
            print(
                "[HH SNAPSHOT LOOP] anti-bot detected; loop stopped.",
                flush=True,
            )
            return 3

        if result.returncode != 0:
            notify_telegram(
                "HH snapshot backfill остановлен из-за ошибки.\n"
                f"Пачка: {batch_no}\n"
                f"Exit code: {result.returncode}\n"
                f"Осталось без snapshot: {remaining_after}"
            )
            return result.returncode

        if remaining_after <= 0:
            notify_telegram(
                "HH snapshot backfill завершён. "
                "Очередь карточек без полного snapshot пуста."
            )
            return 0

        print(
            "[HH SNAPSHOT LOOP] "
            f"sleeping {batch_pause}s before next batch",
            flush=True,
        )
        time.sleep(batch_pause)


if __name__ == "__main__":
    raise SystemExit(main())
