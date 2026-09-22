from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE_PATH = ROOT / "data" / "secrets" / "sber_screening.sqlite3"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SberScreeningStore:
    """Local-only state for GigaRecruiter screening.

    This database intentionally lives under data/secrets so it is not part of
    the main hh_agent.db snapshot uploaded to observability/Google Drive.
    """

    def __init__(self, path: Path | str = DEFAULT_STORE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    external_message_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT,
                    suggested_answer TEXT,
                    confidence TEXT,
                    reason TEXT,
                    approved_payload TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(session_id, external_message_id)
                );

                CREATE INDEX IF NOT EXISTS ix_sber_sessions_status
                    ON sessions(status);
                CREATE INDEX IF NOT EXISTS ix_sber_turns_status
                    ON turns(status);
                """
            )

    def arm(self, application_id: int) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET status='superseded', updated_at=? "
                "WHERE status IN ('armed', 'active')",
                (now,),
            )
            cursor = connection.execute(
                "INSERT INTO sessions(application_id, status, created_at, updated_at) "
                "VALUES (?, 'armed', ?, ?)",
                (application_id, now, now),
            )
            session_id = int(cursor.lastrowid)
        return self.get_session(session_id)

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_active_session(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions "
                "WHERE status IN ('armed', 'active') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def set_session_active(self, session_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET status='active', updated_at=? WHERE id=?",
                (_now(), session_id),
            )

    def create_turn(
        self,
        *,
        session_id: int,
        external_message_id: int,
        question: str,
        options: list[str] | None = None,
        suggested_answer: str | None = None,
        confidence: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM turns WHERE session_id=? AND external_message_id=?",
                (session_id, external_message_id),
            ).fetchone()
            if existing:
                return dict(existing)

            cursor = connection.execute(
                """
                INSERT INTO turns(
                    session_id, external_message_id, question, options_json,
                    suggested_answer, confidence, reason, approved_payload,
                    status, created_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?, NULL)
                """,
                (
                    session_id,
                    external_message_id,
                    question,
                    json.dumps(options or [], ensure_ascii=False),
                    suggested_answer,
                    confidence,
                    reason,
                    now,
                ),
            )
            turn_id = int(cursor.lastrowid)
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE id=?",
                (turn_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_suggestion(
        self,
        turn_id: int,
        *,
        suggested_answer: str | None,
        confidence: str,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE turns SET suggested_answer=?, confidence=?, reason=? "
                "WHERE id=? AND status='pending'",
                (
                    suggested_answer,
                    confidence,
                    (reason or "")[:1000],
                    turn_id,
                ),
            )

    def approve_text(self, turn_id: int, text: str) -> bool:
        value = (text or "").strip()
        if not value:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE turns SET approved_payload=?, status='approved' "
                "WHERE id=? AND status='pending'",
                ("text:" + value, turn_id),
            )
        return cursor.rowcount > 0

    def approve_suggestion(self, turn_id: int) -> bool:
        turn = self.get_turn(turn_id)
        if not turn or not (turn.get("suggested_answer") or "").strip():
            return False
        return self.approve_text(turn_id, str(turn["suggested_answer"]))

    def approve_button(self, turn_id: int, index: int) -> bool:
        turn = self.get_turn(turn_id)
        if not turn or turn.get("status") != "pending":
            return False
        try:
            options = json.loads(turn.get("options_json") or "[]")
        except json.JSONDecodeError:
            return False
        if not isinstance(options, list) or not (0 <= index < len(options)):
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE turns SET approved_payload=?, status='approved' "
                "WHERE id=? AND status='pending'",
                (f"button:{index}", turn_id),
            )
        return cursor.rowcount > 0

    def reject(self, turn_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE turns SET status='rejected' "
                "WHERE id=? AND status='pending'",
                (turn_id,),
            )
        return cursor.rowcount > 0

    def approved_turns(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE status='approved' ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_sent(self, turn_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE turns SET status='sent', sent_at=? WHERE id=?",
                (_now(), turn_id),
            )

    def mark_error(self, turn_id: int, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE turns SET status='error', reason=? WHERE id=?",
                ((reason or "")[:1000], turn_id),
            )

    def pending_turns(self, session_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM turns WHERE status='pending'"
        params: tuple[Any, ...] = ()
        if session_id is not None:
            query += " AND session_id=?"
            params = (session_id,)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def history(self, session_id: int, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result
