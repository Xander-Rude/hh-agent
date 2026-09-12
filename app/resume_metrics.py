from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "hh_agent.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS resume_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_id TEXT NOT NULL,
            observed_at DATETIME NOT NULL,
            views INTEGER,
            invitations INTEGER,
            raises_delta INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_resume_metrics_resume_observed "
        "ON resume_metrics(resume_id, observed_at)"
    )
    return connection


def _now_stamp() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def record_snapshot(
    resume_id: str,
    *,
    views: int | None,
    invitations: int | None,
) -> None:
    if views is None and invitations is None:
        return
    with _connect() as connection:
        connection.execute(
            "INSERT INTO resume_metrics "
            "(resume_id, observed_at, views, invitations, raises_delta) "
            "VALUES (?, ?, ?, ?, 0)",
            (resume_id, _now_stamp(), views, invitations),
        )


def record_raises(resume_id: str, count: int) -> None:
    count = int(count or 0)
    if count <= 0:
        return
    with _connect() as connection:
        connection.execute(
            "INSERT INTO resume_metrics "
            "(resume_id, observed_at, views, invitations, raises_delta) "
            "VALUES (?, ?, NULL, NULL, ?)",
            (resume_id, _now_stamp(), count),
        )
