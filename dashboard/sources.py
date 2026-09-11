"""Bounded readers for existing SQLite, runtime JSON and log files."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock


MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")
RESUME_ID = "ed318343ff109278200039ed1f674d474e5336"
EXPERIMENT = {
    "resume_id": RESUME_ID,
    "title": "Руководитель IT-программ и портфеля проектов",
    "start": "2026-09-12",
    "provenance": "Параметры эксперимента, заданные пользователем",
    "configuration_verified": None,
    "views": None,
    "invitations": None,
    "raises": None,
}
WORKERS = ("pipeline", "apply", "telegram", "resume_raise")
LOGS = (
    "pipeline_supervisor.log", "collector.log", "careers_collector.log",
    "processor.log", "apply_supervisor.log", "apply_dispatcher.log",
    "apply_worker.log", "apply_worker_attention.log", "apply_worker_runtime.log",
    "yandex_apply_worker.log", "yandex_apply_worker_attention.log",
    "vk_apply_worker.log", "vk_apply_worker_attention.log", "telegram.log",
    "resume_raise_supervisor.log", "resume_raise_worker.log",
)
STATE_FIELDS = (
    "status", "stage", "started_at", "finished_at", "pid", "last_error",
    "updated_at", "exit_code", "triggered_by", "next_due_at", "schedule_reason",
)


def safe_path(root: Path, relative: str) -> Path:
    """Do not follow a source symlink/junction outside its configured root."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Source escapes configured root")
    return path


def read_runtime(root: Path, name: str, now: datetime) -> dict:
    if name not in WORKERS:
        raise ValueError("Unknown worker")
    result = {"name": name, "source": f"data/runtime/{name}.json",
              "availability": "unavailable", "state": None, "age_seconds": None,
              "process_alive": None, "heartbeat": None}
    try:
        with safe_path(root, result["source"]).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Oversized state")
        state = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(state, dict):
            raise ValueError("State must be an object")
        # State files merge previous values. Old completion fields may survive a new run.
        state = {key: value for key, value in state.items()
                 if key in STATE_FIELDS and isinstance(value, (str, int, type(None)))}
        if state.get("status") in ("starting", "running"):
            state.pop("finished_at", None)
            state.pop("exit_code", None)
        result.update(availability="available", state=state)
        try:
            updated = datetime.fromisoformat(state.get("updated_at") or "")
            if updated.tzinfo is not None:
                age = (now - updated).total_seconds()
                if age >= 0:
                    result["age_seconds"] = round(age)
        except (TypeError, ValueError):
            pass
    except FileNotFoundError:
        result["reason"] = "Файл состояния отсутствует"
    except (OSError, ValueError, RecursionError):
        result["reason"] = "Файл состояния недоступен или повреждён"
    return result


def read_log(root: Path, name: str, lines: int = 80) -> dict:
    if name not in LOGS:
        raise ValueError("Unknown log")
    lines = max(1, min(lines, 200))
    result = {"name": name, "source": f"logs/{name}", "lines": None,
              "availability": "unavailable", "modified_at": None}
    try:
        with safe_path(root, result["source"]).open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            offset = max(0, size - 65536)
            stream.seek(offset)
            raw = stream.read(65536)
        if offset:
            # Discard the potentially partial first line (including partial UTF-8).
            raw = raw.partition(b"\n")[2]
        result.update(availability="available", lines=raw.decode("utf-8-sig", "replace").splitlines()[-lines:],
                      truncated=bool(offset),
                      modified_at=datetime.fromtimestamp(
                          safe_path(root, result["source"]).stat().st_mtime, UTC).isoformat())
    except FileNotFoundError:
        result["reason"] = "Лог отсутствует"
    except (OSError, ValueError):
        result["reason"] = "Лог недоступен"
    return result


@contextmanager
def readonly_connection(path: Path):
    # mode=ro never creates the DB. Do not import app.db: it runs migrations on import.
    # Do not use immutable/nolock: the agent can write concurrently, including in WAL mode.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 0.20
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        yield connection
    finally:
        connection.close()


def read_database(root: Path, now: datetime) -> dict:
    local_day = now.astimezone(MOSCOW).date()
    day_start = datetime.combine(local_day, datetime.min.time(), MOSCOW).astimezone(UTC)
    stamp = lambda value: value.replace(tzinfo=None).isoformat(sep=" ")
    result = {
        "source": "data/hh_agent.db", "availability": "unavailable",
        "sampled_at": now.isoformat(), "day": str(local_day), "timezone": "Europe/Moscow (UTC+03:00)",
        "counters": {"vacancies": None, "evaluations": None, "applications": None},
        "application_statuses": None, "apply_today": None,
        "source_counts": None, "application_daily": None, "evaluation_scores": None,
        "experiment": {**EXPERIMENT, "applied_since_start": None}, "issues": [],
    }
    try:
        with readonly_connection(safe_path(root, result["source"])) as connection:
            # One bounded read snapshot: concurrent commits cannot split the counters.
            connection.execute("BEGIN")
            # Fixed table/column names only; no SQL or paths are accepted from HTTP.
            schema = {}
            for table in result["counters"]:
                schema[table] = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not schema[table]:
                    result["issues"].append(f"{table}: таблица отсутствует")
                    continue
                result["counters"][table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            columns = schema["applications"]
            if "source" in schema["vacancies"]:
                result["source_counts"] = [
                    {"source": source, "count": count}
                    for source, count in connection.execute(
                        "SELECT source, count(*) FROM vacancies GROUP BY source ORDER BY count(*) DESC LIMIT 50")
                ]
            if "score" in schema["evaluations"]:
                result["evaluation_scores"] = [
                    {"band": band, "count": count}
                    for band, count in connection.execute(
                        "SELECT min(CAST(score / 10 AS INTEGER), 9), count(*) FROM evaluations "
                        "WHERE score BETWEEN 0 AND 100 GROUP BY 1 ORDER BY 1")
                ]
            if "status" in columns:
                result["application_statuses"] = [
                    {"status": status, "count": count}
                    for status, count in connection.execute(
                        "SELECT status, count(*) FROM applications GROUP BY status ORDER BY count(*) DESC LIMIT 50")
                ]
            if {"status", "applied_at"} <= columns:
                result["apply_today"] = connection.execute(
                    "SELECT count(*) FROM applications WHERE status='applied' AND applied_at >= ? AND applied_at < ?",
                    (stamp(day_start), stamp(day_start + timedelta(days=1))),
                ).fetchone()[0]
                first_day = day_start - timedelta(days=13)
                daily = dict(connection.execute(
                    "SELECT date(applied_at, '+3 hours'), count(*) FROM applications "
                    "WHERE status='applied' AND applied_at >= ? AND applied_at < ? GROUP BY 1",
                    (stamp(first_day), stamp(day_start + timedelta(days=1))),
                ))
                result["application_daily"] = [
                    {"day": str(local_day - timedelta(days=offset)),
                     "count": daily.get(str(local_day - timedelta(days=offset)), 0)}
                    for offset in range(13, -1, -1)
                ]
            else:
                result["issues"].append("apply today: нет status/applied_at")
            if {"status", "applied_at", "selected_resume_id"} <= columns:
                experiment_start = datetime(2026, 9, 12, tzinfo=MOSCOW).astimezone(UTC)
                result["experiment"]["applied_since_start"] = connection.execute(
                    "SELECT count(*) FROM applications WHERE status='applied' AND selected_resume_id=? "
                    "AND applied_at >= ? AND applied_at <= ?",
                    (RESUME_ID, stamp(experiment_start), stamp(now.astimezone(UTC))),
                ).fetchone()[0]
            else:
                result["issues"].append("experiment: нет полей для привязки отклика к резюме")
            result["availability"] = "partial" if result["issues"] else "available"
    except (sqlite3.Error, OSError, ValueError):
        # A partial read must not masquerade as a complete fresh snapshot.
        result.update(availability="unavailable", apply_today=None, application_statuses=None)
        result.update(source_counts=None, application_daily=None, evaluation_scores=None)
        result["counters"] = dict.fromkeys(result["counters"])
        result["experiment"]["applied_since_start"] = None
        result["issues"] = ["БД отсутствует, занята, недоступна или превышен лимит чтения"]
    return result


class SnapshotReader:
    """One short DB read per 30 seconds per server, regardless of browser count."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = Lock()
        self._db = None
        self._expires = 0.0
        self._day = None

    def snapshot(self) -> dict:
        now = datetime.now(UTC)
        today = now.astimezone(MOSCOW).date()
        with self._lock:
            if self._db is None or time.monotonic() >= self._expires or self._day != today:
                self._db = read_database(self.root, now)
                self._expires = time.monotonic() + 30
                self._day = today
            database = self._db
        return {"sampled_at": now.isoformat(), "read_only": True,
                "source_root": str(self.root), "database": database,
                "workers": [read_runtime(self.root, name, now) for name in WORKERS],
                "logs": list(LOGS)}
