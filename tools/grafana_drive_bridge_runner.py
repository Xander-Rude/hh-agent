from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# pythonw.exe has no attached console. Keep logging safe by giving the bridge
# writable sinks even when stdout/stderr are absent.
_DEVNULL_HANDLES = []
for stream_name in ("stdout", "stderr"):
    if getattr(sys, stream_name) is None:
        handle = open(os.devnull, "w", encoding="utf-8")
        _DEVNULL_HANDLES.append(handle)
        setattr(sys, stream_name, handle)

import grafana_drive_bridge as bridge  # noqa: E402


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "hh_agent.db"


def parse_runner_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remote", default=bridge.DEFAULT_REMOTE)
    parser.add_argument("--state-dir", type=Path, default=bridge.DEFAULT_STATE_DIR)
    parser.add_argument("--rclone", type=Path, default=bridge.DEFAULT_RCLONE)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args, _ = parser.parse_known_args()
    return args


def subprocess_window_kwargs() -> dict[str, int]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_rclone(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=240,
        **subprocess_window_kwargs(),
    )


def hidden_rclone_upload(
    rclone_path: Path,
    remote: str,
    files: dict[str, Path],
) -> None:
    if not rclone_path.exists():
        raise RuntimeError(f"rclone was not found: {rclone_path}")

    remote = remote.rstrip("/")
    for path in files.values():
        target = f"{remote}/{path.name}"
        proc = run_rclone(
            [
                str(rclone_path),
                "copyto",
                str(path),
                target,
                "--retries",
                "3",
                "--low-level-retries",
                "5",
                "--timeout",
                "60s",
            ]
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"rclone upload failed for {path.name}: {detail[:1500]}"
            )


def create_sqlite_snapshot(source_db: Path, target_db: Path) -> Path:
    """Create a transactionally consistent SQLite snapshot.

    Using sqlite3.Connection.backup() is safe while hh-agent is writing and
    also includes WAL-backed changes. A raw filesystem copy would not provide
    that guarantee.
    """
    if not source_db.exists():
        raise RuntimeError(f"HH Agent database was not found: {source_db}")

    target_db.parent.mkdir(parents=True, exist_ok=True)
    temp_db = target_db.with_suffix(target_db.suffix + ".tmp")
    try:
        temp_db.unlink(missing_ok=True)
        source = sqlite3.connect(str(source_db), timeout=30)
        target = sqlite3.connect(str(temp_db), timeout=30)
        try:
            source.backup(target)
            row = target.execute("PRAGMA quick_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise RuntimeError(f"SQLite snapshot quick_check failed: {row}")
        finally:
            target.close()
            source.close()

        temp_db.replace(target_db)
        return target_db
    finally:
        temp_db.unlink(missing_ok=True)


def _group_count(connection: sqlite3.Connection, sql: str) -> dict[str, int]:
    try:
        return {
            str(key if key is not None else "unknown"): int(count)
            for key, count in connection.execute(sql).fetchall()
        }
    except sqlite3.OperationalError:
        return {}


def write_db_summary(snapshot_db: Path, target_json: Path) -> Path:
    connection = sqlite3.connect(str(snapshot_db), timeout=30)
    try:
        technical = _group_count(
            connection,
            "SELECT status, COUNT(*) FROM applications GROUP BY status",
        )
        career = _group_count(
            connection,
            "SELECT COALESCE(career_state, 'unknown'), COUNT(*) "
            "FROM applications GROUP BY COALESCE(career_state, 'unknown')",
        )
        events = _group_count(
            connection,
            "SELECT event_type, COUNT(*) FROM application_events GROUP BY event_type",
        )

        try:
            human_contacts = int(
                connection.execute(
                    "SELECT COUNT(*) FROM application_events "
                    "WHERE is_human_contact = 1"
                ).fetchone()[0]
            )
        except sqlite3.OperationalError:
            human_contacts = 0

        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "technical_statuses": technical,
            "career_states": career,
            "application_events": events,
            "verified_human_contacts": human_contacts,
            "semantics": {
                "workflow_invitation": (
                    "HH workflow state only; not a verified human contact or interview"
                ),
                "verified_human_contacts": (
                    "Only events explicitly marked is_human_contact=1"
                ),
            },
        }
    finally:
        connection.close()

    bridge.atomic_write_text(
        target_json,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return target_json


def safe_log_name(value: object) -> str | None:
    if not value:
        return None
    name = Path(str(value).replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return None
    return name


def write_per_log_files(state_dir: Path) -> Path:
    source = state_dir / "hh-agent-last-24h.jsonl"
    if not source.exists():
        raise RuntimeError(f"Combined snapshot was not found: {source}")

    grouped: dict[str, list[str]] = defaultdict(list)
    with source.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            record = json.loads(raw_line)
            name = safe_log_name(record.get("file"))
            if not name:
                continue
            grouped[name].append(str(record.get("line", "")))

    logs_dir = state_dir / "logs"
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    for name, lines in sorted(grouped.items()):
        path = logs_dir / name
        text = "\n".join(lines)
        if text:
            text += "\n"
        path.write_text(text, encoding="utf-8", newline="\n")

    logging.info("Prepared per-log snapshots: files=%s dir=%s", len(grouped), logs_dir)
    return logs_dir


def sync_per_log_files(rclone_path: Path, remote: str, logs_dir: Path) -> None:
    if not rclone_path.exists():
        raise RuntimeError(f"rclone was not found: {rclone_path}")

    target = f"{remote.rstrip('/')}/logs"
    proc = run_rclone(
        [
            str(rclone_path),
            "sync",
            str(logs_dir),
            target,
            "--retries",
            "3",
            "--low-level-retries",
            "5",
            "--timeout",
            "60s",
        ]
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"rclone per-log sync failed: {detail[:1500]}")

    logging.info("Per-log snapshots synced to %s", target)


def main() -> int:
    args = parse_runner_args()

    try:
        # The scheduled task itself is windowless via pythonw.exe, but rclone.exe
        # is a console application. Override the bridge uploader so every rclone
        # child process is created with CREATE_NO_WINDOW on Windows as well.
        bridge.rclone_upload = hidden_rclone_upload

        result = bridge.main()
        if result != 0:
            return int(result)

        logs_dir = write_per_log_files(args.state_dir)
        sync_per_log_files(args.rclone, args.remote, logs_dir)

        snapshot_path = create_sqlite_snapshot(
            args.db_path,
            args.state_dir / "hh-agent-db-snapshot.sqlite3",
        )
        db_summary_path = write_db_summary(
            snapshot_path,
            args.state_dir / "hh-agent-db-summary.json",
        )
        hidden_rclone_upload(
            args.rclone,
            args.remote,
            {
                "db_snapshot": snapshot_path,
                "db_summary": db_summary_path,
            },
        )
        logging.info(
            "SQLite snapshot synced to %s",
            args.remote.rstrip("/"),
        )
        return 0
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        try:
            logging.exception("Bridge runner failed: %s", exc)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
