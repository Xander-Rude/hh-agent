from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "HH_AGENT_DB_PATH",
        str(ROOT / "data" / "hh_agent.db"),
    )
)
SNAPSHOT_NAME = "hh-agent-db-snapshot.sqlite3"
FUNNEL_NAME = "hh-agent-funnel-snapshot.json"
DEFAULT_MIN_INTERVAL_MINUTES = max(
    1,
    int(os.environ.get("HH_DB_SNAPSHOT_MIN_INTERVAL", "15")),
)


def snapshot_due(
    state_dir: Path,
    *,
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES,
) -> bool:
    path = state_dir / SNAPSHOT_NAME
    if not path.exists():
        return True
    age_seconds = max(0.0, time.time() - path.stat().st_mtime)
    return age_seconds >= min_interval_minutes * 60


def _atomic_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def create_sqlite_snapshot(
    db_path: Path,
    destination: Path,
) -> Path:
    """Create a consistent SQLite snapshot using SQLite's online backup API."""
    if not db_path.exists():
        raise FileNotFoundError(f"HH Agent database was not found: {db_path}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    temp_path.unlink(missing_ok=True)

    source_uri = f"file:{db_path.as_posix()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=30)
    target = sqlite3.connect(temp_path, timeout=30)
    try:
        source.backup(target)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(
                f"SQLite snapshot integrity check failed: {integrity}"
            )
    finally:
        target.close()
        source.close()

    _atomic_replace(temp_path, destination)
    return destination


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _dict_rows(
    connection: sqlite3.Connection,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, params)
    columns = [item[0] for item in cursor.description]
    return [
        dict(zip(columns, row, strict=False))
        for row in cursor.fetchall()
    ]


def build_funnel_export(snapshot_path: Path) -> dict[str, Any]:
    """Return a compact analysis-friendly export without vacancy descriptions."""
    connection = sqlite3.connect(snapshot_path)
    try:
        applications = _dict_rows(
            connection,
            """
            SELECT
                a.id AS application_id,
                a.status AS technical_status,
                COALESCE(a.career_status, 'unknown') AS career_status,
                a.applied_at,
                a.created_at AS application_created_at,
                a.response_checked_at,
                COALESCE(a.manual_recovery_attempts, 0) AS manual_recovery_attempts,
                a.manual_recovery_last_at,
                v.id AS vacancy_id,
                v.source,
                v.external_id,
                v.hh_id,
                v.title,
                v.company,
                v.url,
                v.salary_from,
                v.salary_to,
                v.salary_currency,
                v.published_at,
                v.found_at,
                e.score,
                e.decision,
                e.role_match,
                e.seniority_match,
                e.domain_match,
                e.responsibility_match,
                e.selected_resume_title,
                e.selected_resume_score,
                e.created_at AS evaluation_created_at
            FROM applications AS a
            JOIN vacancies AS v ON v.id = a.vacancy_id
            LEFT JOIN evaluations AS e
              ON e.id = (
                SELECT e2.id
                FROM evaluations AS e2
                WHERE e2.vacancy_id = v.id
                  AND e2.model NOT LIKE 'hard-filter/%'
                ORDER BY e2.created_at DESC, e2.id DESC
                LIMIT 1
              )
            ORDER BY a.id
            """,
        )

        events_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if _table_exists(connection, "application_events"):
            for event in _dict_rows(
                connection,
                """
                SELECT
                    id,
                    application_id,
                    event_type,
                    source,
                    details,
                    observed_at
                FROM application_events
                ORDER BY application_id, observed_at, id
                """,
            ):
                events_by_application[int(event["application_id"])].append(event)

        for application in applications:
            application["events"] = events_by_application.get(
                int(application["application_id"]),
                [],
            )

        resume_metrics = []
        if _table_exists(connection, "resume_metrics"):
            resume_metrics = _dict_rows(
                connection,
                """
                SELECT resume_id, observed_at, views, invitations, raises_delta
                FROM resume_metrics
                ORDER BY observed_at
                """,
            )

        technical_counts = Counter(
            str(item.get("technical_status") or "unknown")
            for item in applications
        )
        career_counts = Counter(
            str(item.get("career_status") or "unknown")
            for item in applications
        )

        return {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "semantics": {
                "technical_status": (
                    "Automation/apply transport state. applied means HH submit "
                    "was confirmed, not that a recruiter responded."
                ),
                "career_status": (
                    "Post-apply funnel state. workflow_invited is an HH/employer "
                    "workflow label and is explicitly NOT a human response or interview."
                ),
                "resume_metrics.invitations": (
                    "Raw HH resume-card counter only; do not use as an interview KPI."
                ),
            },
            "counts": {
                "applications": len(applications),
                "technical_status": dict(sorted(technical_counts.items())),
                "career_status": dict(sorted(career_counts.items())),
                "events": sum(len(items) for items in events_by_application.values()),
                "resume_metric_snapshots": len(resume_metrics),
            },
            "applications": applications,
            "resume_metrics": resume_metrics,
        }
    finally:
        connection.close()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    _atomic_replace(temp_path, path)
    return path


def create_snapshot_outputs(
    state_dir: Path,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Path]:
    state_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = create_sqlite_snapshot(
        db_path,
        state_dir / SNAPSHOT_NAME,
    )
    funnel_path = _write_json_atomic(
        state_dir / FUNNEL_NAME,
        build_funnel_export(sqlite_path),
    )
    return {
        "database": sqlite_path,
        "funnel": funnel_path,
    }
