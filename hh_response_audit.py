"""Strict read-only audit of applicant responses on HH.

The script reuses the shared Playwright ``browser-profile`` and never imports
the apply worker. Browser traffic is guarded to GET/HEAD/OPTIONS only. The
official negotiation message-list endpoint is intentionally not read because
HH may clear ``has_updates`` when that list is viewed. Chat history is read
through chatik's GET-only topic endpoint; HH read receipts are a separate
``POST /chatik/api/mark_read`` and remain blocked.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from playwright.sync_api import APIResponse, BrowserContext, Page, sync_playwright

from background_common import AgentLock
from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "hh_response_audit.sqlite"
RESPONSES_CSV_PATH = DATA_DIR / "hh_responses.csv"
EVENTS_CSV_PATH = DATA_DIR / "hh_response_events.csv"

NEGOTIATIONS_PAGE_URL = "https://hh.ru/applicant/negotiations"
NEGOTIATIONS_API_URL = "https://api.hh.ru/negotiations"
CHATIK_BASE_URL = "https://chatik.hh.ru"
CHATIK_TOPIC_DATA_URL = f"{CHATIK_BASE_URL}/chatik/api/chat_data_by_topic"
CHATIK_CHAT_DATA_URL = f"{CHATIK_BASE_URL}/chatik/api/chat_data"
CHATIK_CHATS_URL = f"{CHATIK_BASE_URL}/chatik/api/chats"
DEFAULT_LIMIT = 10
DEFAULT_DELAY_SECONDS = max(
    0.5,
    float(os.getenv("HH_AUDIT_DELAY_SECONDS", "1.5")),
)
API_PAGE_SIZE = 50
REQUEST_TIMEOUT_MS = 30_000

SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TERMINAL_STATE_MARKERS = (
    "discard",
    "reject",
    "archiv",
    "closed",
    "deleted",
    "отказ",
    "закрыт",
    "архив",
)
INVITE_STATE_MARKERS = (
    "invite",
    "invitation",
    "interview",
    "offer",
    "phone_interview",
    "приглаш",
    "интервью",
    "предложение",
)
VIEW_MARKERS = (
    "резюме просмотрено",
    "работодатель просмотрел резюме",
    "работодатель просмотрел ваше резюме",
    "ваше резюме просмотрено",
)
REJECTION_MARKERS = (
    "работодатель отказал",
    "вам отказали",
    "отказ по отклику",
)
INVITE_MARKERS = (
    "приглашение",
    "работодатель пригласил",
    "вас пригласили",
)

EMPLOYER_ACK_MARKERS = (
    "рассмотрим ваше резюме",
    "рассмотрим ваше резюме",
    "мы взяли ваше резюме в работу",
    "мы взяли вашe резюме в работу",
    "взяли ваше резюме в работу",
    "мы сохранили ваше резюме",
    "сохранили ваше резюме",
    "если навыки и опыт подойдут",
    "если ваши навыки и опыт подойдут",
    "в случае положительного решения",
    "свяжемся с вами, если",
    "свяжемся с вами если",
)


class HHChallengeError(RuntimeError):
    """HH captcha / anti-bot challenge detected during a read-only audit."""


CHALLENGE_URL_MARKERS = (
    "/captcha",
    "/challenge",
    "captcha=",
)
CHALLENGE_TEXT_MARKERS = (
    "подтвердите, что вы не робот",
    "подтвердите, что вы человек",
    "проверка безопасности",
    "verify you are human",
    "are you a robot",
    "captcha",
)


def challenge_reason(url: Any, title: Any = None, body: Any = None) -> str | None:
    url_text = clean_text(url).lower()
    title_text = clean_text(title).lower()
    body_text = clean_text(body).lower()

    if any(marker in url_text for marker in CHALLENGE_URL_MARKERS):
        return f"challenge_url={url_text[:180]}"

    combined = f"{title_text}\n{body_text}"
    for marker in CHALLENGE_TEXT_MARKERS:
        if marker in combined:
            return f"challenge_marker={marker}"
    return None


def raise_if_hh_challenge(page: Page) -> None:
    try:
        url = page.url
    except Exception:
        url = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        body = clean_text(page.locator("body").inner_text(timeout=2500))[:12000]
    except Exception:
        body = ""

    reason = challenge_reason(url, title, body)
    if reason:
        raise HHChallengeError(reason)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def normalize_url(url: str | None, base: str = "https://hh.ru") -> str | None:
    value = clean_text(url)
    if not value:
        return None
    return urljoin(base, value)


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def nested(data: Any, *keys: str, default: Any = None) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def state_id(value: Any) -> str:
    if isinstance(value, dict):
        return clean_text(value.get("id") or value.get("name")).lower()
    return clean_text(value).lower()


def state_name(value: Any) -> str:
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("id"))
    return clean_text(value)


def looks_rejected(value: Any) -> bool:
    text = state_id(value)
    return any(
        marker in text
        for marker in ("discard", "reject", "rejected", "отказ")
    )


def looks_invited(value: Any) -> bool:
    text = state_id(value)
    return any(marker in text for marker in INVITE_STATE_MARKERS)


def extract_negotiation_id(url_or_id: Any) -> str | None:
    value = clean_text(url_or_id)
    if not value:
        return None
    if value.isdigit():
        return value

    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for key in ("negotiation_id", "negotiationId", "nid", "response_id", "responseId"):
        values = query.get(key)
        if values and clean_text(values[0]):
            return clean_text(values[0])

    patterns = (
        r"/negotiations/([A-Za-z0-9_-]+)(?:/|$)",
        r"/negotiation/([A-Za-z0-9_-]+)(?:/|$)",
        r"/chat/([A-Za-z0-9_-]+)(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return None


def extract_vacancy_id(url_or_id: Any) -> str | None:
    value = clean_text(url_or_id)
    if not value:
        return None
    if value.isdigit():
        return value
    match = re.search(r"/vacancy/(\d+)", value)
    if match:
        return match.group(1)
    parsed = urlparse(value)
    for key in ("vacancy_id", "vacancyId"):
        values = parse_qs(parsed.query).get(key)
        if values and clean_text(values[0]):
            return clean_text(values[0])
    return None


def web_negotiation_url(negotiation_id: str | None) -> str | None:
    if not negotiation_id:
        return None
    return f"https://hh.ru/applicant/negotiations/{negotiation_id}"


def stable_event_id(
    application_id: str,
    *,
    source_event_id: Any = None,
    timestamp: Any = None,
    author: Any = None,
    event_type: Any = None,
    text: Any = None,
) -> str:
    source_value = clean_text(source_event_id)
    if source_value:
        payload = f"{application_id}|source:{source_value}"
    else:
        payload = "|".join(
            [
                application_id,
                clean_text(timestamp),
                clean_text(author).lower(),
                clean_text(event_type).lower(),
                clean_text(text),
            ]
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class RateLimiter:
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    _last_request_at: float = 0.0

    def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        now = time.monotonic()
        remaining = self.delay_seconds - (now - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining + random.uniform(0.0, min(0.35, self.delay_seconds / 3)))
        self._last_request_at = time.monotonic()


class ReadOnlyRequestGuard:
    """Hard browser-side guard against requests that can change HH state.

    POST/PUT/PATCH/DELETE are always blocked. HH also has several GET
    endpoints with side effects (for example marking a chat as read), so those
    are blocked explicitly too. The auditor itself never calls click/fill/press.
    """

    UNSAFE_GET_MARKERS = (
        "/chatik/api/mark_read",
        "/chatik/api/notify_chat_opened",
        "/chatik/api/get_or_create",
        "/chatik/api/participant_action",
        "/chatik/api/send_event",
        "/chatik/api/rate_chat",
        "/shards/vacancy/register_interaction",
    )

    @classmethod
    def should_block(cls, method: str, url: str) -> bool:
        normalized_method = clean_text(method).upper()
        normalized_url = clean_text(url).lower()

        if normalized_method not in SAFE_HTTP_METHODS:
            return True

        if normalized_method != "GET":
            return False

        if any(marker in normalized_url for marker in cls.UNSAFE_GET_MARKERS):
            return True

        # Reading negotiation/chat message lists can acknowledge unread
        # messages in HH. Keep them out of a strict read-only audit.
        if re.search(r"/negotiations/[^/?#]+/messages(?:[/?#]|$)", normalized_url):
            return True
        if "/chatik/api/messages" in normalized_url:
            return True

        return False

    def __init__(self) -> None:
        self.blocked: list[dict[str, str]] = []

    def install(self, context: BrowserContext) -> None:
        def handle(route, request) -> None:
            method = clean_text(request.method).upper()
            url = clean_text(request.url)
            if not self.should_block(method, url):
                route.continue_()
                return
            self.blocked.append(
                {
                    "method": method,
                    "url": url,
                }
            )
            route.abort()

        context.route("**/*", handle)


class AuditStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS responses (
                application_id TEXT PRIMARY KEY,
                negotiation_id TEXT,
                vacancy_id TEXT,
                vacancy_title TEXT,
                company TEXT,
                vacancy_url TEXT,
                chat_negotiation_url TEXT,
                applied_at TEXT,
                current_status TEXT,
                viewed_by_employer INTEGER,
                viewed_at TEXT,
                employer_replied INTEGER,
                first_reply_at TEXT,
                rejected INTEGER,
                invited INTEGER,
                active_dialog INTEGER,
                messages_count INTEGER,
                last_message_at TEXT,
                source TEXT,
                source_updated_at TEXT,
                raw_json TEXT,
                first_collected_at TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                detail_collected_at TEXT
            );

            CREATE INDEX IF NOT EXISTS ix_responses_applied_at
                ON responses(applied_at);
            CREATE INDEX IF NOT EXISTS ix_responses_vacancy_id
                ON responses(vacancy_id);

            CREATE TABLE IF NOT EXISTS response_events (
                event_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                source_event_id TEXT,
                timestamp TEXT,
                author TEXT,
                event_type TEXT,
                text TEXT,
                raw_json TEXT,
                collected_at TEXT NOT NULL,
                FOREIGN KEY(application_id)
                    REFERENCES responses(application_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_response_events_application
                ON response_events(application_id, timestamp);

            CREATE TABLE IF NOT EXISTS audit_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                mode TEXT NOT NULL,
                requested_limit INTEGER,
                phase TEXT NOT NULL,
                next_list_page INTEGER NOT NULL DEFAULT 0,
                source_mode TEXT,
                status TEXT NOT NULL,
                blocked_mutating_requests INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_queue (
                run_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                application_id TEXT NOT NULL,
                detail_url TEXT,
                processed INTEGER NOT NULL DEFAULT 0,
                processed_at TEXT,
                PRIMARY KEY(run_id, position),
                UNIQUE(run_id, application_id),
                FOREIGN KEY(run_id)
                    REFERENCES audit_runs(run_id)
                    ON DELETE CASCADE
            );
            """
        )
        self.conn.commit()

    def create_run(self, *, mode: str, requested_limit: int | None) -> str:
        run_id = uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO audit_runs (
                run_id, started_at, mode, requested_limit, phase, status
            ) VALUES (?, ?, ?, ?, 'discover', 'running')
            """,
            (run_id, now_iso(), mode, requested_limit),
        )
        self.conn.commit()
        return run_id

    def resumable_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM audit_runs
            WHERE status IN ('running', 'failed')
              AND finished_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()

    def get_run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM audit_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"audit_run_not_found:{run_id}")
        return row

    def update_run(self, run_id: str, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{name} = ?" for name in values)
        params = [*values.values(), run_id]
        self.conn.execute(
            f"UPDATE audit_runs SET {columns} WHERE run_id = ?",
            params,
        )
        self.conn.commit()

    def mark_run_failed(self, run_id: str, exc: BaseException) -> None:
        self.update_run(
            run_id,
            status="failed",
            last_error=f"{type(exc).__name__}: {exc}",
        )

    def mark_run_done(self, run_id: str, blocked_count: int) -> None:
        self.update_run(
            run_id,
            status="done",
            phase="done",
            finished_at=now_iso(),
            blocked_mutating_requests=blocked_count,
            last_error=None,
        )

    def queued_count(self, run_id: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit_queue WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )

    def enqueue(
        self,
        run_id: str,
        *,
        position: int,
        application_id: str,
        detail_url: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_queue (
                run_id, position, application_id, detail_url
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, application_id) DO UPDATE SET
                detail_url = COALESCE(excluded.detail_url, audit_queue.detail_url)
            """,
            (run_id, position, application_id, detail_url),
        )
        self.conn.commit()

    def pending_queue(self, run_id: str) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM audit_queue
            WHERE run_id = ? AND processed = 0
            ORDER BY position
            """,
            (run_id,),
        )

    def mark_processed(self, run_id: str, position: int) -> None:
        self.conn.execute(
            """
            UPDATE audit_queue
            SET processed = 1, processed_at = ?
            WHERE run_id = ? AND position = ?
            """,
            (now_iso(), run_id, position),
        )
        self.conn.commit()

    def upsert_response(self, record: dict[str, Any]) -> None:
        application_id = clean_text(record.get("application_id"))
        if not application_id:
            raise ValueError("application_id is required")

        timestamp = now_iso()
        current = self.conn.execute(
            "SELECT * FROM responses WHERE application_id = ?",
            (application_id,),
        ).fetchone()

        defaults = {
            "negotiation_id": None,
            "vacancy_id": None,
            "vacancy_title": None,
            "company": None,
            "vacancy_url": None,
            "chat_negotiation_url": None,
            "applied_at": None,
            "current_status": None,
            "viewed_by_employer": None,
            "viewed_at": None,
            "employer_replied": None,
            "first_reply_at": None,
            "rejected": None,
            "invited": None,
            "active_dialog": None,
            "messages_count": None,
            "last_message_at": None,
            "source": None,
            "source_updated_at": None,
            "raw_json": None,
            "detail_collected_at": None,
        }

        merged: dict[str, Any] = {}
        for key, fallback in defaults.items():
            incoming = record.get(key, None)
            if incoming is not None:
                merged[key] = incoming
            elif current is not None:
                merged[key] = current[key]
            else:
                merged[key] = fallback

        if current is None:
            columns = [
                "application_id",
                *defaults.keys(),
                "first_collected_at",
                "collected_at",
            ]
            values = [
                application_id,
                *[merged[key] for key in defaults],
                timestamp,
                timestamp,
            ]
            placeholders = ", ".join("?" for _ in columns)
            self.conn.execute(
                f"""
                INSERT INTO responses ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                values,
            )
        else:
            assignments = ", ".join(f"{key} = ?" for key in defaults)
            self.conn.execute(
                f"""
                UPDATE responses
                SET {assignments}, collected_at = ?
                WHERE application_id = ?
                """,
                [*[merged[key] for key in defaults], timestamp, application_id],
            )
        self.conn.commit()

    def upsert_event(self, event: dict[str, Any]) -> None:
        application_id = clean_text(event.get("application_id"))
        if not application_id:
            raise ValueError("event.application_id is required")
        event_id = clean_text(event.get("event_id")) or stable_event_id(
            application_id,
            source_event_id=event.get("source_event_id"),
            timestamp=event.get("timestamp"),
            author=event.get("author"),
            event_type=event.get("event_type"),
            text=event.get("text"),
        )
        self.conn.execute(
            """
            INSERT INTO response_events (
                event_id, application_id, source_event_id, timestamp,
                author, event_type, text, raw_json, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                timestamp = COALESCE(excluded.timestamp, response_events.timestamp),
                author = COALESCE(excluded.author, response_events.author),
                event_type = COALESCE(excluded.event_type, response_events.event_type),
                text = COALESCE(excluded.text, response_events.text),
                raw_json = COALESCE(excluded.raw_json, response_events.raw_json),
                collected_at = excluded.collected_at
            """,
            (
                event_id,
                application_id,
                clean_text(event.get("source_event_id")) or None,
                clean_text(event.get("timestamp")) or None,
                clean_text(event.get("author")) or None,
                clean_text(event.get("event_type")) or None,
                clean_text(event.get("text")) or None,
                event.get("raw_json"),
                now_iso(),
            ),
        )
        self.conn.commit()


def vacancy_from_item(item: dict[str, Any]) -> dict[str, Any]:
    vacancy = item.get("vacancy")
    if not isinstance(vacancy, dict):
        vacancy = {}
    employer = vacancy.get("employer")
    if not isinstance(employer, dict):
        employer = {}
    vacancy_id = clean_text(
        first_nonempty(
            vacancy.get("id"),
            extract_vacancy_id(vacancy.get("alternate_url")),
            extract_vacancy_id(vacancy.get("url")),
        )
    ) or None
    vacancy_url = normalize_url(vacancy.get("alternate_url"), base="https://hh.ru")
    if not vacancy_url and vacancy_id:
        vacancy_url = f"https://hh.ru/vacancy/{vacancy_id}"
    return {
        "vacancy_id": vacancy_id,
        "vacancy_title": clean_text(vacancy.get("name")) or None,
        "company": clean_text(employer.get("name")) or None,
        "vacancy_url": vacancy_url,
    }


def response_from_api_item(item: dict[str, Any]) -> dict[str, Any]:
    negotiation_id = clean_text(
        first_nonempty(
            item.get("id"),
            extract_negotiation_id(item.get("url")),
        )
    )
    if not negotiation_id:
        raise ValueError("HH negotiation item has no id")

    status = state_name(item.get("state")) or state_id(item.get("state")) or None
    result = {
        "application_id": negotiation_id,
        "negotiation_id": negotiation_id,
        **vacancy_from_item(item),
        "chat_negotiation_url": web_negotiation_url(negotiation_id),
        "applied_at": clean_text(item.get("created_at")) or None,
        "current_status": status,
        "viewed_by_employer": (
            int(bool(item.get("viewed_by_opponent")))
            if "viewed_by_opponent" in item
            else None
        ),
        "rejected": int(looks_rejected(item.get("state"))),
        "invited": int(looks_invited(item.get("state"))),
        "source": "hh_api",
        "source_updated_at": clean_text(item.get("updated_at")) or None,
        "raw_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
    }
    return result


def response_from_api_detail(
    existing: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    """Merge GET /negotiations/{id} without opening the message list.

    HH documents that viewing the message list can clear ``has_updates``.
    A strict read-only audit therefore reads only the negotiation resource and
    its counters, never ``/messages``.
    """
    result = dict(existing)
    result.update(vacancy_from_item(detail))

    negotiation_id = clean_text(
        first_nonempty(
            detail.get("id"),
            existing.get("negotiation_id"),
            existing.get("application_id"),
        )
    )
    status = first_nonempty(
        state_name(detail.get("state")) or None,
        existing.get("current_status"),
    )
    counters = detail.get("counters")
    if not isinstance(counters, dict):
        counters = {}

    messages_count = counters.get("messages")
    if not isinstance(messages_count, int):
        messages_count = existing.get("messages_count")

    rejected = bool(existing.get("rejected")) or looks_rejected(detail.get("state"))
    invited = bool(existing.get("invited")) or looks_invited(detail.get("state"))
    chat_id = detail.get("chat_id")
    messaging_status = clean_text(detail.get("messaging_status")).lower()

    result.update(
        {
            "application_id": negotiation_id,
            "negotiation_id": negotiation_id,
            "chat_negotiation_url": first_nonempty(
                existing.get("chat_negotiation_url"),
                web_negotiation_url(negotiation_id),
            ),
            "applied_at": first_nonempty(
                clean_text(detail.get("created_at")) or None,
                existing.get("applied_at"),
            ),
            "current_status": status,
            "viewed_by_employer": (
                int(bool(detail.get("viewed_by_opponent")))
                if "viewed_by_opponent" in detail
                else existing.get("viewed_by_employer")
            ),
            "employer_replied": (
                1
                if invited or rejected
                else existing.get("employer_replied")
            ),
            "rejected": int(rejected),
            "invited": int(invited),
            "active_dialog": int(
                bool(chat_id)
                and messaging_status not in {"disabled", "archived", "closed"}
                and not rejected
            ),
            "messages_count": messages_count,
            "source": "hh_api",
            "source_updated_at": first_nonempty(
                clean_text(detail.get("updated_at")) or None,
                existing.get("source_updated_at"),
            ),
            "raw_json": json.dumps(detail, ensure_ascii=False, sort_keys=True),
            "detail_collected_at": now_iso(),
        }
    )
    return result


def status_event_from_detail(
    record: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any] | None:
    application_id = clean_text(record.get("application_id"))
    state = detail.get("state")
    status_id = state_id(state)
    if not application_id or not status_id or status_id == "response":
        return None

    if looks_rejected(state):
        event_type = "rejection"
        author = "employer"
    elif looks_invited(state):
        event_type = "employer_invite"
        author = "employer"
    else:
        event_type = "status_change"
        author = "system"

    return {
        "application_id": application_id,
        "source_event_id": f"current-state:{application_id}:{status_id}",
        "timestamp": clean_text(detail.get("updated_at")) or None,
        "author": author,
        "event_type": event_type,
        "text": state_name(state) or status_id,
        "raw_json": json.dumps(state, ensure_ascii=False, sort_keys=True),
    }


def viewed_event_from_detail(
    record: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any] | None:
    application_id = clean_text(record.get("application_id"))
    if not application_id or not bool(detail.get("viewed_by_opponent")):
        return None
    return {
        "application_id": application_id,
        "source_event_id": f"resume-viewed:{application_id}",
        "timestamp": None,
        "author": "employer",
        "event_type": "resume_viewed",
        "text": "HH сообщает, что отклик просмотрен работодателем",
        "raw_json": None,
    }


def application_submitted_event(record: dict[str, Any]) -> dict[str, Any] | None:
    application_id = clean_text(record.get("application_id"))
    applied_at = clean_text(record.get("applied_at"))
    if not application_id or not applied_at:
        return None
    return {
        "application_id": application_id,
        "source_event_id": f"negotiation-created:{application_id}",
        "timestamp": applied_at,
        "author": "applicant",
        "event_type": "application_submitted",
        "text": "Отклик создан на HH",
        "raw_json": None,
    }


def status_event_from_record(
    record: dict[str, Any],
) -> dict[str, Any] | None:
    application_id = clean_text(record.get("application_id"))
    status = clean_text(record.get("current_status"))
    status_id = state_id(status)
    if not application_id or not status_id or status_id == "response":
        return None

    if looks_rejected(status):
        event_type = "rejection"
        author = "employer"
    elif looks_invited(status):
        event_type = "employer_invite"
        author = "employer"
    else:
        event_type = "status_change"
        author = "system"

    return {
        "application_id": application_id,
        "source_event_id": f"current-state:{application_id}:{status_id}",
        "timestamp": clean_text(record.get("source_updated_at")) or None,
        "author": author,
        "event_type": event_type,
        "text": status,
        "raw_json": None,
    }


def viewed_event_from_record(
    record: dict[str, Any],
) -> dict[str, Any] | None:
    application_id = clean_text(record.get("application_id"))
    if not application_id or not bool(record.get("viewed_by_employer")):
        return None
    return {
        "application_id": application_id,
        "source_event_id": f"resume-viewed:{application_id}",
        "timestamp": clean_text(record.get("viewed_at")) or None,
        "author": "employer",
        "event_type": "resume_viewed",
        "text": "HH сообщает, что отклик просмотрен работодателем",
        "raw_json": None,
    }


def api_get_json(
    context: BrowserContext,
    url: str,
    *,
    limiter: RateLimiter,
    user_agent: str,
    retries: int = 3,
) -> tuple[int, Any]:
    last_status = 0
    for attempt in range(retries):
        limiter.wait()
        response: APIResponse = context.request.get(
            url,
            headers={"HH-User-Agent": user_agent, "User-Agent": user_agent},
            timeout=REQUEST_TIMEOUT_MS,
        )
        last_status = int(response.status)
        if last_status == 200:
            try:
                return last_status, response.json()
            except Exception:
                return last_status, None
        if last_status not in {429, 500, 502, 503, 504}:
            return last_status, None
        time.sleep(min(8.0, 1.5 * (2**attempt)))
    return last_status, None


def _context_cookie_value(
    context: BrowserContext,
    name: str,
) -> str:
    try:
        cookies = context.cookies(
            [
                "https://hh.ru",
                "https://chatik.hh.ru",
            ]
        )
    except Exception:
        return ""

    wanted = name.lower()
    for cookie in cookies:
        if clean_text(cookie.get("name")).lower() == wanted:
            return clean_text(cookie.get("value"))
    return ""


def chatik_topic_url(application_id: str) -> str:
    topic_id = clean_text(application_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", topic_id):
        raise ValueError(f"unsafe topic id: {topic_id!r}")
    return f"{CHATIK_TOPIC_DATA_URL}?topicId={topic_id}"


def chatik_chat_url(chat_id: str) -> str:
    value = clean_text(chat_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"unsafe chat id: {value!r}")
    return f"{CHATIK_CHAT_DATA_URL}?chatId={value}"


def _chatik_headers(
    context: BrowserContext,
    user_agent: str,
) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, */*",
        "Referer": f"{CHATIK_BASE_URL}/",
        "Origin": CHATIK_BASE_URL,
    }
    xsrf = _context_cookie_value(context, "_xsrf")
    if xsrf:
        headers["X-XSRFToken"] = xsrf
    return headers


def _chat_resource_ids(
    item: Any,
    *resource_names: str,
) -> set[str]:
    if not isinstance(item, dict):
        return set()
    resources = item.get("resources")
    if not isinstance(resources, dict):
        return set()

    ids: set[str] = set()
    for resource_name in resource_names:
        values = resources.get(resource_name)
        if not isinstance(values, list):
            values = [values] if values not in (None, "", {}, []) else []
        for value in values:
            if isinstance(value, dict):
                value = first_nonempty(
                    value.get("topicId"),
                    value.get("topic_id"),
                    value.get("negotiationId"),
                    value.get("negotiation_id"),
                    value.get("vacancyId"),
                    value.get("vacancy_id"),
                    value.get("id"),
                )
            resource_id = clean_text(value)
            if resource_id:
                ids.add(resource_id)
    return ids


def _chat_negotiation_topic_ids(item: Any) -> set[str]:
    return _chat_resource_ids(
        item,
        "NEGOTIATION_TOPIC",
        "negotiation_topic",
        "negotiationTopic",
    )


def _chat_vacancy_ids(item: Any) -> set[str]:
    return _chat_resource_ids(
        item,
        "VACANCY",
        "vacancy",
        "vacancies",
    )


def _chat_topic_ids(item: Any) -> set[str]:
    if not isinstance(item, dict):
        return set()

    ids = set(_chat_negotiation_topic_ids(item))
    fallback = clean_text(
        first_nonempty(
            item.get("topicId"),
            item.get("topic_id"),
            item.get("negotiationId"),
            item.get("negotiation_id"),
            item.get("id"),
        )
    )
    if fallback:
        ids.add(fallback)
    return ids


def _chat_unique_entries(
    index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in index.values():
        chat_id = clean_text(entry.get("chat_id"))
        topic_id = clean_text(entry.get("topic_id"))
        key = (chat_id, topic_id)
        if key == ("", ""):
            continue
        unique[key] = entry
    return list(unique.values())


def chatik_match_entry(
    record: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for candidate in (
        record.get("application_id"),
        record.get("negotiation_id"),
    ):
        candidate_id = clean_text(candidate)
        if candidate_id and candidate_id in index:
            return index[candidate_id]

    vacancy_id = clean_text(record.get("vacancy_id"))
    if not vacancy_id:
        return None

    matches = [
        entry
        for entry in _chat_unique_entries(index)
        if vacancy_id in set(entry.get("vacancy_ids") or [])
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _chat_item_has_activity(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    unread = item.get("unreadCount")
    if isinstance(unread, int) and unread > 0:
        return True

    last_message = first_nonempty(
        item.get("lastMessage"),
        item.get("last_message"),
    )
    if not isinstance(last_message, dict):
        return False
    return any(
        last_message.get(key) not in (None, "", {}, [])
        for key in (
            "id",
            "text",
            "createdAt",
            "created_at",
            "workflowTransition",
            "workflow_transition",
        )
    )


def chatik_build_topic_index(
    context: BrowserContext,
    *,
    limiter: RateLimiter,
    user_agent: str,
    max_pages: int = 100,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Read the chat list once and index negotiation topics.

    This is a GET-only prefilter. Per-topic chat history is fetched only for
    topics whose chat list item shows actual activity.
    """
    headers = _chatik_headers(context, user_agent)
    index: dict[str, dict[str, Any]] = {}
    cursor = ""

    for page_number in range(max_pages):
        if cursor:
            url = f"{CHATIK_CHATS_URL}?cursor={quote(cursor, safe='')}"
        else:
            url = f"{CHATIK_CHATS_URL}?page={page_number}"

        limiter.wait()
        response: APIResponse = context.request.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT_MS,
        )
        status = int(response.status)
        if status == 429:
            raise HHChallengeError("chatik_http_429_rate_limited")
        if status != 200:
            return index, f"chatik_chats_status={status}"

        try:
            payload = response.json()
        except Exception:
            return index, "chatik_chats_invalid_json"
        if not isinstance(payload, dict):
            return index, "chatik_chats_invalid_payload"

        chats = payload.get("chats")
        if not isinstance(chats, dict):
            chats = payload

        items = chats.get("items") if isinstance(chats, dict) else None
        if not isinstance(items, list):
            return index, "chatik_chats_items_missing"

        for item in items:
            if not isinstance(item, dict):
                continue
            has_activity = _chat_item_has_activity(item)
            chat_id = clean_text(item.get("id")) or None
            negotiation_topic_ids = _chat_negotiation_topic_ids(item)
            explicit_topic_id = clean_text(
                first_nonempty(
                    item.get("topicId"),
                    item.get("topic_id"),
                    item.get("negotiationId"),
                    item.get("negotiation_id"),
                )
            ) or None
            canonical_topic_id = (
                sorted(negotiation_topic_ids)[0]
                if negotiation_topic_ids
                else explicit_topic_id
            )
            vacancy_ids = sorted(_chat_vacancy_ids(item))
            entry = {
                "chat_id": chat_id,
                "topic_id": canonical_topic_id,
                "topic_ids": sorted(negotiation_topic_ids),
                "vacancy_ids": vacancy_ids,
                "has_activity": has_activity,
            }
            for alias_id in _chat_topic_ids(item):
                existing = index.get(alias_id)
                if existing is None or (
                    has_activity and not bool(existing.get("has_activity"))
                ):
                    index[alias_id] = entry

        if chats.get("hasNextPage") is False:
            break

        next_page = chats.get("nextPage")
        if isinstance(next_page, str) and next_page:
            cursor = next_page
            continue

        per_page = chats.get("perPage", 20)
        if not isinstance(per_page, int) or per_page <= 0:
            per_page = 20
        if len(items) < per_page:
            break

    return index, None


def print_chat_index_diagnostics(
    store: AuditStore,
    index: dict[str, dict[str, Any]],
) -> None:
    response_rows = store.conn.execute(
        "SELECT application_id, negotiation_id, vacancy_id FROM responses"
    ).fetchall()
    response_ids = {
        clean_text(value)
        for row in response_rows
        for value in (row["application_id"], row["negotiation_id"])
        if clean_text(value)
    }
    response_vacancy_ids = {
        clean_text(row["vacancy_id"])
        for row in response_rows
        if clean_text(row["vacancy_id"])
    }

    aliases = set(index)
    direct_overlap = sorted(response_ids & aliases)
    unique_entries = _chat_unique_entries(index)
    active_entries = [
        entry for entry in unique_entries if bool(entry.get("has_activity"))
    ]
    chat_vacancy_ids = {
        clean_text(vacancy_id)
        for entry in unique_entries
        for vacancy_id in (entry.get("vacancy_ids") or [])
        if clean_text(vacancy_id)
    }
    vacancy_overlap = sorted(response_vacancy_ids & chat_vacancy_ids)

    print(
        "[CHAT MATCH] "
        f"aliases={len(index)} "
        f"unique_chats={len(unique_entries)} "
        f"active_chats={len(active_entries)} "
        f"response_ids={len(response_ids)} "
        f"direct_id_overlap={len(direct_overlap)} "
        f"vacancy_overlap={len(vacancy_overlap)}"
    )
    if direct_overlap:
        print(
            "[CHAT MATCH] direct_sample="
            + ",".join(direct_overlap[:10])
        )
    if vacancy_overlap:
        print(
            "[CHAT MATCH] vacancy_sample="
            + ",".join(vacancy_overlap[:10])
        )


def probe_official_detail_api(
    context: BrowserContext,
    application_id: str,
    *,
    limiter: RateLimiter,
    user_agent: str,
) -> bool:
    status, _payload = api_get_json(
        context,
        f"{NEGOTIATIONS_API_URL}/{application_id}",
        limiter=limiter,
        user_agent=user_agent,
        retries=1,
    )
    return status == 200


def chatik_get_topic_json(
    context: BrowserContext,
    application_id: str,
    *,
    limiter: RateLimiter,
    user_agent: str,
    retries: int = 3,
) -> tuple[int, Any]:
    """Read one HH chat topic without acknowledging messages.

    The endpoint only returns chat data. HH uses a separate POST
    /chatik/api/mark_read for read receipts, and all write methods remain
    blocked by this auditor.
    """
    url = chatik_topic_url(application_id)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "chatik.hh.ru"
        or parsed.path != "/chatik/api/chat_data_by_topic"
    ):
        raise RuntimeError("unsafe_chatik_read_url")

    headers = _chatik_headers(context, user_agent)

    last_status = 0
    for attempt in range(retries):
        limiter.wait()
        response: APIResponse = context.request.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT_MS,
        )
        last_status = int(response.status)
        if last_status == 200:
            try:
                return last_status, response.json()
            except Exception:
                return last_status, None
        if last_status not in {429, 500, 502, 503, 504}:
            return last_status, None
        time.sleep(min(8.0, 1.5 * (2**attempt)))
    return last_status, None


def chatik_get_chat_json(
    context: BrowserContext,
    chat_id: str,
    *,
    limiter: RateLimiter,
    user_agent: str,
    retries: int = 3,
) -> tuple[int, Any]:
    """Read one HH chat by chatId without acknowledging messages."""
    url = chatik_chat_url(chat_id)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "chatik.hh.ru"
        or parsed.path != "/chatik/api/chat_data"
    ):
        raise RuntimeError("unsafe_chatik_chat_read_url")

    headers = _chatik_headers(context, user_agent)
    last_status = 0
    for attempt in range(retries):
        limiter.wait()
        response: APIResponse = context.request.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT_MS,
        )
        last_status = int(response.status)
        if last_status == 200:
            try:
                return last_status, response.json()
            except Exception:
                return last_status, None
        if last_status == 429:
            raise HHChallengeError("chatik_http_429_rate_limited")
        if last_status not in {500, 502, 503, 504}:
            return last_status, None
        time.sleep(min(8.0, 1.5 * (2**attempt)))
    return last_status, None


def events_from_chatik_payload(
    application_id: str,
    payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    chat = payload.get("chat")
    if not isinstance(chat, dict):
        chat = payload

    current_participant_id = clean_text(
        first_nonempty(
            chat.get("currentParticipantId"),
            chat.get("current_participant_id"),
            _deep_value_by_aliases(
                payload,
                ("currentParticipantId", "current_participant_id"),
            ),
        )
    )

    messages = nested(chat, "messages", "items", default=[])
    if not isinstance(messages, list):
        messages = _deep_value_by_aliases(
            chat,
            ("messageItems", "message_items"),
        )
    if not isinstance(messages, list):
        messages = []

    events: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        workflow = message.get("workflowTransition")
        if not isinstance(workflow, dict):
            workflow = {}

        workflow_state = clean_text(
            first_nonempty(
                workflow.get("applicantState"),
                workflow.get("applicant_state"),
                workflow.get("state"),
                workflow.get("type"),
                workflow.get("name"),
            )
        )
        workflow_id = clean_text(workflow.get("id"))
        message_type = clean_text(message.get("type"))
        text = clean_text(
            first_nonempty(
                message.get("text"),
                workflow.get("name"),
                workflow.get("title"),
                workflow_state,
                message_type,
                workflow_id,
            )
        )
        if not text:
            continue

        participant_id = clean_text(
            first_nonempty(
                message.get("participantId"),
                message.get("participant_id"),
            )
        )
        participant_display = message.get("participantDisplay")
        if not isinstance(participant_display, dict):
            participant_display = {}
        participant_is_bot = bool(participant_display.get("isBot"))

        system_message_types = {
            "PARTICIPANT_JOINED",
            "PARTICIPANT_LEFT",
            "SYSTEM",
            "SERVICE",
        }
        is_system_message = message_type.upper() in system_message_types
        if is_system_message or workflow_state:
            author = "system"
        elif participant_id and current_participant_id:
            if participant_id == current_participant_id:
                author = "applicant"
            else:
                author = "employer_bot" if participant_is_bot else "employer"
        elif participant_id:
            author = "employer_bot" if participant_is_bot else "employer"
        else:
            author = "system"

        timestamp = clean_text(
            first_nonempty(
                _value_by_aliases(
                    message,
                    (
                        "createdAt",
                        "created_at",
                        "creationTime",
                        "creation_time",
                        "timestamp",
                        "date",
                        "time",
                    ),
                ),
                _deep_value_by_aliases(
                    message,
                    (
                        "createdAt",
                        "created_at",
                        "creationTime",
                        "creation_time",
                        "timestamp",
                    ),
                ),
            )
        ) or None

        explicit_type = first_nonempty(
            workflow_state or None,
            message_type or None,
            workflow_id or None,
        )
        event_type = _event_type(
            text,
            author,
            explicit_type,
        )

        source_event_id = clean_text(
            first_nonempty(
                message.get("id"),
                message.get("messageId"),
                message.get("message_id"),
            )
        ) or None

        event = {
            "application_id": application_id,
            "source_event_id": source_event_id,
            "timestamp": timestamp,
            "author": author,
            "event_type": event_type,
            "text": text,
            "raw_json": json.dumps(
                message,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        event["event_id"] = stable_event_id(
            application_id,
            source_event_id=source_event_id,
            timestamp=timestamp,
            author=author,
            event_type=event_type,
            text=text,
        )
        events.append(event)

    return events


def enrich_from_chatik_payload(
    record: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    application_id = clean_text(record.get("application_id"))
    if not application_id or not isinstance(payload, dict):
        return record, []

    result = dict(record)
    events = events_from_chatik_payload(
        application_id,
        payload,
    )

    chat = payload.get("chat")
    if not isinstance(chat, dict):
        chat = payload

    vacancy_title = clean_text(
        first_nonempty(
            _deep_value_by_aliases(
                payload,
                (
                    "vacancyTitle",
                    "vacancy_title",
                    "vacancyName",
                    "vacancy_name",
                ),
            ),
            result.get("vacancy_title"),
        )
    ) or None

    company = clean_text(
        first_nonempty(
            _deep_value_by_aliases(
                payload,
                (
                    "employerName",
                    "employer_name",
                    "companyName",
                    "company_name",
                ),
            ),
            result.get("company"),
        )
    ) or None

    result.update(
        {
            "vacancy_title": vacancy_title,
            "company": company,
            "messages_count": max(
                int(result.get("messages_count") or 0),
                len(
                    [
                        event
                        for event in events
                        if event.get("event_type")
                        in {"employer_message", "candidate_message"}
                    ]
                ),
            ),
            "detail_collected_at": now_iso(),
        }
    )

    return derive_from_events(result, events), events


def enrich_from_vacancy_page(
    page: Page,
    record: dict[str, Any],
    *,
    limiter: RateLimiter,
) -> dict[str, Any]:
    """Fill vacancy title/company through a guarded GET page when SSR topic lacks them."""
    result = dict(record)
    vacancy_id = clean_text(result.get("vacancy_id"))
    if not vacancy_id or not vacancy_id.isdigit():
        return result
    if result.get("vacancy_title") and result.get("company"):
        return result

    vacancy_url = f"https://hh.ru/vacancy/{vacancy_id}"
    try:
        goto_read_only(
            page,
            vacancy_url,
            limiter=limiter,
        )
    except HHChallengeError:
        raise
    except Exception:
        return result

    def text_by_selectors(selectors: tuple[str, ...]) -> str | None:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() <= 0:
                    continue
                text = clean_text(locator.first.inner_text(timeout=2500))
                if text:
                    return text
            except Exception:
                continue
        return None

    title = text_by_selectors(
        (
            'h1[data-qa="vacancy-title"]',
            '[data-qa="vacancy-title"]',
            "h1",
        )
    )
    company = text_by_selectors(
        (
            '[data-qa="vacancy-company-name"]',
            'a[data-qa="vacancy-company-name"]',
            '[data-qa="vacancy-company"]',
        )
    )

    if not title or not company:
        state = extract_initial_state(page)
        vacancy_view = (
            _deep_find_key(state, "vacancyView")
            if isinstance(state, dict)
            else None
        )
        if isinstance(vacancy_view, dict):
            if not title:
                title = clean_text(
                    first_nonempty(
                        vacancy_view.get("name"),
                        vacancy_view.get("title"),
                        _deep_value_by_aliases(
                            vacancy_view,
                            ("vacancyName", "vacancyTitle"),
                        ),
                    )
                ) or None
            if not company:
                employer = first_nonempty(
                    vacancy_view.get("employer"),
                    _deep_value_by_aliases(vacancy_view, ("employer", "company")),
                )
                if isinstance(employer, dict):
                    company = clean_text(
                        first_nonempty(
                            employer.get("name"),
                            employer.get("title"),
                        )
                    ) or None
                if not company:
                    company = clean_text(
                        _deep_value_by_aliases(
                            vacancy_view,
                            ("employerName", "companyName"),
                        )
                    ) or None

    if title:
        result["vacancy_title"] = title
    if company:
        result["company"] = company
    result["vacancy_url"] = vacancy_url
    return result


def api_list_url(page_number: int) -> str:
    return (
        f"{NEGOTIATIONS_API_URL}"
        f"?page={page_number}&per_page={API_PAGE_SIZE}"
        "&order_by=created_at&order=desc"
    )


def try_discover_api_page(
    context: BrowserContext,
    page_number: int,
    *,
    limiter: RateLimiter,
    user_agent: str,
) -> tuple[str, list[dict[str, Any]], int | None]:
    status, payload = api_get_json(
        context,
        api_list_url(page_number),
        limiter=limiter,
        user_agent=user_agent,
    )
    if status != 200 or not isinstance(payload, dict):
        return "unavailable", [], None

    items = payload.get("items")
    if not isinstance(items, list):
        return "unavailable", [], None

    pages = payload.get("pages")
    page_count = int(pages) if isinstance(pages, int) else None
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            records.append(response_from_api_item(item))
        except ValueError:
            continue
    return "hh_api", records, page_count


def goto_read_only(page: Page, url: str, *, limiter: RateLimiter) -> None:
    limiter.wait()
    response = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.wait_for_timeout(650)

    status = int(response.status) if response is not None else 0
    if status == 429:
        raise HHChallengeError("http_429_rate_limited")
    raise_if_hh_challenge(page)


def extract_initial_state(page: Page) -> dict[str, Any] | None:
    """Read HH SSR state without triggering any extra network request."""
    try:
        value = page.evaluate(
            r"""
            () => {
              const el = document.querySelector(
                'template#HH-Lux-InitialState, script#HH-Lux-InitialState, #HH-Lux-InitialState'
              );
              if (!el) return null;
              const raw = el.textContent || el.innerHTML || '';
              if (!raw.trim()) return null;
              try { return JSON.parse(raw); } catch (_) { return null; }
            }
            """
        )
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _deep_find_key(value: Any, wanted: str) -> Any:
    if isinstance(value, dict):
        if wanted in value:
            return value[wanted]
        for child in value.values():
            found = _deep_find_key(child, wanted)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_find_key(child, wanted)
            if found is not None:
                return found
    return None


def _topic_list_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    negotiations = _deep_find_key(state, "applicantNegotiations")
    candidates: list[Any] = []
    if isinstance(negotiations, dict):
        candidates.extend(
            [
                negotiations.get("topicList"),
                negotiations.get("topics"),
                negotiations.get("items"),
            ]
        )
    candidates.append(_deep_find_key(state, "topicList"))

    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        topics = [item for item in candidate if isinstance(item, dict)]
        if topics:
            return topics
    return []


def _value_by_aliases(data: Any, aliases: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in aliases:
        if key in data and data[key] not in (None, "", [], {}):
            return data[key]
    return None


def _deep_value_by_aliases(
    data: Any,
    aliases: tuple[str, ...],
) -> Any:
    """Breadth-first lookup for HH SSR fields that move between releases."""
    queue: list[Any] = [data]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            object_id = id(current)
            if object_id in seen:
                continue
            seen.add(object_id)

            direct = _value_by_aliases(current, aliases)
            if direct not in (None, "", [], {}):
                return direct

            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)

    return None


def _bool_by_aliases(data: Any, aliases: tuple[str, ...]) -> bool | None:
    value = _value_by_aliases(data, aliases)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _int_by_aliases(data: Any, aliases: tuple[str, ...]) -> int | None:
    value = _value_by_aliases(data, aliases)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def response_from_topic(topic: dict[str, Any]) -> dict[str, Any]:
    negotiation_id = clean_text(
        first_nonempty(
            _value_by_aliases(
                topic,
                (
                    "topicId",
                    "topic_id",
                    "negotiationId",
                    "negotiation_id",
                    "responseId",
                    "response_id",
                    "id",
                ),
            ),
            extract_negotiation_id(
                _deep_value_by_aliases(
                    topic,
                    (
                        "url",
                        "topicUrl",
                        "topic_url",
                        "negotiationUrl",
                        "negotiation_url",
                        "chatUrl",
                        "chat_url",
                    ),
                )
            ),
        )
    )
    if not negotiation_id:
        raise ValueError("HH topic has no negotiation id")

    vacancy = first_nonempty(
        _value_by_aliases(
            topic,
            ("vacancy", "vacancySummary", "vacancyInfo", "vacancyItem"),
        ),
        _deep_value_by_aliases(
            topic,
            ("vacancy", "vacancySummary", "vacancyInfo", "vacancyItem"),
        ),
    )
    if not isinstance(vacancy, dict):
        vacancy = {}

    vacancy_id = clean_text(
        first_nonempty(
            _value_by_aliases(topic, ("vacancyId", "vacancy_id")),
            _value_by_aliases(vacancy, ("id", "vacancyId", "vacancy_id")),
            _deep_value_by_aliases(topic, ("vacancyId", "vacancy_id")),
            extract_vacancy_id(
                _deep_value_by_aliases(
                    topic,
                    (
                        "vacancyUrl",
                        "vacancy_url",
                        "alternate_url",
                    ),
                )
            ),
        )
    ) or None

    vacancy_url = normalize_url(
        first_nonempty(
            _value_by_aliases(topic, ("vacancyUrl", "vacancy_url")),
            _value_by_aliases(
                vacancy,
                ("alternate_url", "url", "vacancyUrl", "vacancy_url"),
            ),
            _deep_value_by_aliases(
                topic,
                ("vacancyUrl", "vacancy_url", "alternate_url"),
            ),
        )
    )
    if not vacancy_url and vacancy_id:
        vacancy_url = f"https://hh.ru/vacancy/{vacancy_id}"

    employer = first_nonempty(
        _value_by_aliases(topic, ("employer", "company")),
        _value_by_aliases(vacancy, ("employer", "company")),
        _deep_value_by_aliases(topic, ("employer", "company")),
    )
    if not isinstance(employer, dict):
        employer = {}

    status_value = first_nonempty(
        _value_by_aliases(
            topic,
            ("state", "status", "applicantState", "lastState"),
        ),
        _value_by_aliases(topic, ("stateName", "statusName")),
        _deep_value_by_aliases(
            topic,
            (
                "applicantState",
                "negotiationState",
                "lastState",
                "stateName",
                "statusName",
            ),
        ),
    )
    status = state_name(status_value) or None

    detail_url = normalize_url(
        first_nonempty(
            _value_by_aliases(
                topic,
                (
                    "url",
                    "topicUrl",
                    "topic_url",
                    "negotiationUrl",
                    "negotiation_url",
                    "chatUrl",
                    "chat_url",
                ),
            ),
            _deep_value_by_aliases(
                topic,
                (
                    "topicUrl",
                    "topic_url",
                    "negotiationUrl",
                    "negotiation_url",
                    "chatUrl",
                    "chat_url",
                ),
            ),
        )
    )
    if detail_url and "/vacancy/" in urlparse(detail_url).path:
        detail_url = None
    if not detail_url:
        detail_url = web_negotiation_url(negotiation_id)

    last_message = first_nonempty(
        _value_by_aliases(
            topic,
            ("lastMessage", "last_message", "latestMessage"),
        ),
        _deep_value_by_aliases(
            topic,
            ("lastMessage", "last_message", "latestMessage"),
        ),
    )
    if not isinstance(last_message, dict):
        last_message = {}

    text_blob = json.dumps(topic, ensure_ascii=False).lower()

    viewed_flag = _bool_by_aliases(
        topic,
        (
            "viewedByOpponent",
            "viewed_by_opponent",
            "resumeViewed",
            "resume_viewed",
            "viewed",
        ),
    )
    if viewed_flag is None:
        deep_viewed = _deep_value_by_aliases(
            topic,
            (
                "viewedByOpponent",
                "viewed_by_opponent",
                "resumeViewed",
                "resume_viewed",
            ),
        )
        if isinstance(deep_viewed, bool):
            viewed_flag = deep_viewed
        elif isinstance(deep_viewed, int):
            viewed_flag = bool(deep_viewed)
    if viewed_flag is None and any(marker in text_blob for marker in VIEW_MARKERS):
        viewed_flag = True

    rejected = looks_rejected(status_value) or any(
        marker in text_blob for marker in REJECTION_MARKERS
    )
    invited = looks_invited(status_value) or any(
        marker in text_blob for marker in INVITE_MARKERS
    )

    messages_count = _int_by_aliases(
        topic,
        ("messagesCount", "messageCount", "messages_count"),
    )
    if messages_count is None:
        deep_count = _deep_value_by_aliases(
            topic,
            ("messagesCount", "messageCount", "messages_count"),
        )
        if isinstance(deep_count, int) and not isinstance(deep_count, bool):
            messages_count = deep_count
        elif isinstance(deep_count, str) and deep_count.isdigit():
            messages_count = int(deep_count)

    applied_at = clean_text(
        first_nonempty(
            _value_by_aliases(
                topic,
                (
                    "createdAt",
                    "created_at",
                    "responseDate",
                    "response_date",
                    "created",
                    "creationTime",
                ),
            ),
            _deep_value_by_aliases(
                topic,
                (
                    "responseDate",
                    "response_date",
                    "createdAt",
                    "created_at",
                    "creationTime",
                    "creation_time",
                ),
            ),
        )
    ) or None

    source_updated_at = clean_text(
        first_nonempty(
            _value_by_aliases(
                topic,
                ("updatedAt", "updated_at", "lastUpdate", "last_update"),
            ),
            _deep_value_by_aliases(
                topic,
                ("updatedAt", "updated_at", "lastUpdate", "last_update"),
            ),
        )
    ) or None

    last_message_at = clean_text(
        first_nonempty(
            _value_by_aliases(
                last_message,
                ("createdAt", "created_at", "timestamp", "date", "time"),
            ),
            _deep_value_by_aliases(
                last_message,
                ("createdAt", "created_at", "timestamp", "date", "time"),
            ),
        )
    ) or None

    vacancy_title = clean_text(
        first_nonempty(
            _value_by_aliases(
                topic,
                ("vacancyName", "vacancyTitle", "vacancy_name", "vacancy_title"),
            ),
            _value_by_aliases(vacancy, ("name", "title")),
            _deep_value_by_aliases(
                topic,
                ("vacancyName", "vacancyTitle", "vacancy_name", "vacancy_title"),
            ),
        )
    ) or None

    company = clean_text(
        first_nonempty(
            _value_by_aliases(
                topic,
                ("employerName", "companyName", "company_name"),
            ),
            _value_by_aliases(employer, ("name", "title")),
            _deep_value_by_aliases(
                topic,
                ("employerName", "companyName", "company_name"),
            ),
        )
    ) or None

    return {
        "application_id": negotiation_id,
        "negotiation_id": negotiation_id,
        "vacancy_id": vacancy_id,
        "vacancy_title": vacancy_title,
        "company": company,
        "vacancy_url": vacancy_url,
        "chat_negotiation_url": detail_url,
        "applied_at": applied_at,
        "current_status": status,
        "viewed_by_employer": (
            int(viewed_flag) if viewed_flag is not None else None
        ),
        "rejected": int(bool(rejected)),
        "invited": int(bool(invited)),
        "messages_count": messages_count,
        "last_message_at": last_message_at,
        "source": "hh_ssr",
        "source_updated_at": source_updated_at,
        "raw_json": json.dumps(topic, ensure_ascii=False, sort_keys=True),
    }


def extract_ssr_list_records(page: Page) -> list[dict[str, Any]]:
    state = extract_initial_state(page)
    if not state:
        return []
    records: list[dict[str, Any]] = []
    for topic in _topic_list_from_state(state):
        try:
            records.append(response_from_topic(topic))
        except ValueError:
            continue
    return records


def extract_dom_list_records(page: Page) -> list[dict[str, Any]]:
    rows = page.evaluate(
        r"""
        () => {
          const absolute = (href) => {
            try { return new URL(href, location.href).href; } catch (_) { return null; }
          };
          const cards = Array.from(
            document.querySelectorAll('[data-qa="negotiations-item"]')
          );
          return cards.map((card) => {
            const vacancy = card.querySelector(
              'a[data-qa="negotiations-item-vacancy-link"], a[href*="/vacancy/"]'
            );
            const employer = card.querySelector(
              '[data-qa*="employer"], a[href*="/employer/"]'
            );
            const status = card.querySelector(
              '[data-qa*="status"], [data-qa*="state"], [data-qa*="badge"]'
            );
            const links = Array.from(card.querySelectorAll('a[href]'))
              .map((a) => absolute(a.getAttribute('href')))
              .filter(Boolean);
            const attrs = {};
            for (const attr of Array.from(card.attributes || [])) {
              attrs[attr.name] = attr.value;
            }
            return {
              vacancy_url: vacancy ? absolute(vacancy.getAttribute('href')) : null,
              vacancy_title: vacancy ? (vacancy.textContent || '').trim() : null,
              company: employer ? (employer.textContent || '').trim() : null,
              status: status ? (status.textContent || '').trim() : null,
              text: (card.innerText || '').trim(),
              links,
              attrs
            };
          });
        }
        """
    )
    records: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return records

    for row in rows:
        if not isinstance(row, dict):
            continue
        links = row.get("links")
        if not isinstance(links, list):
            links = []
        attrs = row.get("attrs")
        if not isinstance(attrs, dict):
            attrs = {}

        negotiation_id = None
        detail_url = None
        for candidate in [
            *links,
            *attrs.values(),
        ]:
            candidate_id = extract_negotiation_id(candidate)
            if candidate_id:
                negotiation_id = candidate_id
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://", "/")):
                    detail_url = normalize_url(candidate)
                break

        if not negotiation_id:
            for key in (
                "data-topic-id",
                "data-negotiation-id",
                "data-response-id",
                "data-id",
            ):
                value = clean_text(attrs.get(key))
                if value:
                    negotiation_id = value
                    break

        if not negotiation_id:
            continue

        vacancy_url = normalize_url(row.get("vacancy_url"))
        vacancy_id = extract_vacancy_id(vacancy_url)
        status = clean_text(row.get("status")) or None
        text = clean_text(row.get("text"))
        lowered = text.lower()

        records.append(
            {
                "application_id": negotiation_id,
                "negotiation_id": negotiation_id,
                "vacancy_id": vacancy_id,
                "vacancy_title": clean_text(row.get("vacancy_title")) or None,
                "company": clean_text(row.get("company")) or None,
                "vacancy_url": vacancy_url,
                "chat_negotiation_url": detail_url or web_negotiation_url(negotiation_id),
                "current_status": status,
                "viewed_by_employer": (
                    1 if any(marker in lowered for marker in VIEW_MARKERS) else None
                ),
                "rejected": int(
                    looks_rejected(status)
                    or any(marker in lowered for marker in REJECTION_MARKERS)
                ),
                "invited": int(
                    looks_invited(status)
                    or any(marker in lowered for marker in INVITE_MARKERS)
                ),
                "source": "hh_dom",
                "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    return records


def list_page_url(page_number: int) -> str:
    if page_number <= 0:
        return f"{NEGOTIATIONS_PAGE_URL}?filter=all"
    return f"{NEGOTIATIONS_PAGE_URL}?filter=all&page={page_number}"


def _event_author(data: dict[str, Any]) -> str:
    author = first_nonempty(
        data.get("author"),
        data.get("sender"),
        data.get("participant"),
        data.get("from"),
    )
    if isinstance(author, dict):
        author = first_nonempty(
            author.get("participant_type"),
            author.get("participantType"),
            author.get("type"),
            author.get("role"),
            author.get("name"),
        )
    value = clean_text(author).lower()
    if value in {"applicant", "candidate", "соискатель"}:
        return "applicant"
    if value in {"employer", "manager", "recruiter", "работодатель"}:
        return "employer"
    if value in {"system", "hh", "robot", "bot"}:
        return "system"

    is_mine = first_nonempty(data.get("isMine"), data.get("is_mine"))
    if is_mine is True:
        return "applicant"
    if is_mine is False:
        return "employer"
    return clean_text(author) or "system"


def looks_like_employer_acknowledgement(text: Any) -> bool:
    lowered = clean_text(text).lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in EMPLOYER_ACK_MARKERS)


def _event_type(text: str, author: str, explicit: Any = None) -> str:
    explicit_text = clean_text(explicit).lower()
    combined = f"{explicit_text} {text.lower()}"

    if any(marker in combined for marker in VIEW_MARKERS):
        return "resume_viewed"
    if looks_rejected(combined):
        return "rejection"
    if looks_invited(combined):
        return "employer_invite"
    if explicit_text in {"application", "response"}:
        return "application_submitted"
    if "response" in combined and "created" in combined:
        return "application_submitted"
    if author == "employer_bot":
        return "employer_bot_message"
    if author == "employer" and looks_like_employer_acknowledgement(text):
        return "employer_acknowledgement"
    if author == "employer":
        return "employer_message"
    if author == "applicant":
        return "candidate_message"
    return explicit_text or "system_event"


def _event_timestamp(data: dict[str, Any]) -> str | None:
    value = first_nonempty(
        _value_by_aliases(
            data,
            (
                "createdAt",
                "created_at",
                "timestamp",
                "date",
                "time",
                "updatedAt",
                "updated_at",
            ),
        ),
        nested(data, "created", "at"),
    )
    return clean_text(value) or None


def _event_text(data: dict[str, Any]) -> str:
    value = first_nonempty(
        _value_by_aliases(
            data,
            (
                "text",
                "message",
                "body",
                "content",
                "description",
                "label",
                "title",
            ),
        ),
        state_name(data.get("state")) if "state" in data else None,
    )
    if isinstance(value, dict):
        value = first_nonempty(value.get("text"), value.get("value"), value.get("name"))
    return clean_text(value)


def extract_events_from_payload(
    application_id: str,
    payload: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_objects: set[int] = set()

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            object_id = id(value)
            if object_id in seen_objects:
                return
            seen_objects.add(object_id)

            lowered_path = path.lower()
            interesting_path = any(
                token in lowered_path
                for token in (
                    "message",
                    "event",
                    "history",
                    "timeline",
                    "transition",
                    "chat",
                    "state",
                    "topic",
                )
            )
            text = _event_text(value)
            timestamp = _event_timestamp(value)
            author = _event_author(value)
            source_event_id = clean_text(
                first_nonempty(
                    _value_by_aliases(
                        value,
                        (
                            "messageId",
                            "message_id",
                            "eventId",
                            "event_id",
                            "historyId",
                            "id",
                        ),
                    ),
                    None,
                )
            ) or None
            explicit_type = first_nonempty(
                _value_by_aliases(
                    value,
                    ("eventType", "event_type", "type", "kind", "stateType"),
                ),
                state_id(value.get("state")) if "state" in value else None,
            )

            has_author_field = any(
                key in value
                for key in ("author", "sender", "participant", "from", "isMine", "is_mine")
            )
            if (
                interesting_path
                and text
                and (timestamp or source_event_id or has_author_field)
            ):
                event_type = _event_type(text, author, explicit_type)
                events.append(
                    {
                        "application_id": application_id,
                        "source_event_id": source_event_id,
                        "timestamp": timestamp,
                        "author": author,
                        "event_type": event_type,
                        "text": text,
                        "raw_json": json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                )

            for key, child in value.items():
                walk(child, f"{path}.{key}")
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload, "root")

    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        key = stable_event_id(
            application_id,
            source_event_id=event.get("source_event_id"),
            timestamp=event.get("timestamp"),
            author=event.get("author"),
            event_type=event.get("event_type"),
            text=event.get("text"),
        )
        event["event_id"] = key
        deduped[key] = event
    return list(deduped.values())


def extract_dom_events(
    page: Page,
    application_id: str,
) -> list[dict[str, Any]]:
    try:
        rows = page.evaluate(
            r"""
            () => {
              const nodes = Array.from(document.querySelectorAll(
                '[data-qa*="message"], [data-qa*="event"], [data-qa*="history"]'
              ));
              return nodes.slice(0, 500).map((node) => {
                const time = node.querySelector('time, [datetime], [data-qa*="time"]');
                return {
                  qa: node.getAttribute('data-qa'),
                  text: (node.innerText || '').trim(),
                  datetime: time
                    ? (time.getAttribute('datetime') || time.textContent || '').trim()
                    : null,
                  className: node.className || ''
                };
              }).filter((item) => item.text);
            }
            """
        )
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return events

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        text = clean_text(row.get("text"))
        if not text:
            continue
        meta = f"{row.get('qa') or ''} {row.get('className') or ''}".lower()
        if any(token in meta for token in ("applicant", "mine", "outgoing")):
            author = "applicant"
        elif any(token in meta for token in ("employer", "incoming", "manager")):
            author = "employer"
        else:
            author = "system"
        event = {
            "application_id": application_id,
            "source_event_id": None,
            "timestamp": clean_text(row.get("datetime")) or None,
            "author": author,
            "event_type": _event_type(text, author, row.get("qa")),
            "text": text,
            "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        }
        event["event_id"] = stable_event_id(
            application_id,
            timestamp=event["timestamp"],
            author=author,
            event_type=event["event_type"],
            text=text,
        )
        events.append(event)
    return events


def _parse_timestamp(value: Any) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.timestamp()
    except Exception:
        return None


def _first_time(values: Iterable[Any]) -> str | None:
    candidates = [clean_text(value) for value in values if clean_text(value)]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            _parse_timestamp(item) is None,
            _parse_timestamp(item) or 0.0,
            item,
        ),
    )


def _last_time(values: Iterable[Any]) -> str | None:
    candidates = [clean_text(value) for value in values if clean_text(value)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            _parse_timestamp(item) is not None,
            _parse_timestamp(item) or 0.0,
            item,
        ),
    )


def derive_from_events(
    record: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    result = dict(record)
    submitted_events = [
        event for event in events if event.get("event_type") == "application_submitted"
    ]
    viewed_events = [
        event for event in events if event.get("event_type") == "resume_viewed"
    ]
    employer_messages = [
        event for event in events if event.get("event_type") == "employer_message"
    ]
    acknowledgement_events = [
        event
        for event in events
        if event.get("event_type") == "employer_acknowledgement"
    ]
    bot_messages = [
        event
        for event in events
        if event.get("event_type") == "employer_bot_message"
    ]
    rejection_events = [
        event for event in events if event.get("event_type") == "rejection"
    ]
    invite_events = [
        event for event in events if event.get("event_type") == "employer_invite"
    ]
    message_events = [
        event
        for event in events
        if event.get("event_type")
        in {
            "employer_message",
            "candidate_message",
            "employer_acknowledgement",
            "employer_bot_message",
        }
    ]

    if submitted_events and not result.get("applied_at"):
        result["applied_at"] = _first_time(
            event.get("timestamp") for event in submitted_events
        )
    if viewed_events:
        result["viewed_by_employer"] = 1
        result["viewed_at"] = _first_time(
            event.get("timestamp") for event in viewed_events
        )
    if employer_messages:
        result["employer_replied"] = 1
        result["first_reply_at"] = _first_time(
            event.get("timestamp") for event in employer_messages
        )
    if rejection_events:
        result["rejected"] = 1
        result["employer_replied"] = 1
        if not result.get("first_reply_at"):
            result["first_reply_at"] = _first_time(
                event.get("timestamp") for event in rejection_events
            )
    if invite_events:
        result["invited"] = 1
        result["employer_replied"] = 1
        if not result.get("first_reply_at"):
            result["first_reply_at"] = _first_time(
                event.get("timestamp") for event in invite_events
            )
    if message_events:
        result["messages_count"] = max(
            int(result.get("messages_count") or 0),
            len(message_events),
        )
        result["last_message_at"] = _last_time(
            event.get("timestamp") for event in message_events
        )

    status_text = clean_text(result.get("current_status")).lower()
    if any(marker in status_text for marker in VIEW_MARKERS):
        result["viewed_by_employer"] = 1
    if looks_rejected(status_text):
        result["rejected"] = 1
        result["employer_replied"] = 1
    if looks_invited(status_text):
        result["invited"] = 1
        result["employer_replied"] = 1

    # Recompute on every pass so stale values from earlier parser versions do
    # not survive after event reclassification. Generic acknowledgements and
    # bot messages are kept as history but do not count as a substantive
    # employer reply or an active dialogue.
    substantive_contact = bool(employer_messages or invite_events)
    result["active_dialog"] = int(
        substantive_contact
        and not bool(result.get("rejected"))
    )
    return result


def _find_payload_for_negotiation(
    value: Any,
    negotiation_id: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1

    def walk(node: Any) -> None:
        nonlocal best, best_score
        if isinstance(node, dict):
            candidate_id = clean_text(
                first_nonempty(
                    _value_by_aliases(
                        node,
                        (
                            "id",
                            "topicId",
                            "topic_id",
                            "negotiationId",
                            "negotiation_id",
                            "responseId",
                            "response_id",
                        ),
                    ),
                    extract_negotiation_id(
                        _value_by_aliases(
                            node,
                            ("url", "topicUrl", "negotiationUrl", "chatUrl"),
                        )
                    ),
                )
            )
            if candidate_id == negotiation_id:
                score = sum(
                    1
                    for key in (
                        "vacancy",
                        "state",
                        "status",
                        "messages",
                        "history",
                        "chat",
                        "lastMessage",
                    )
                    if key in node
                )
                if score > best_score:
                    best = node
                    best_score = score
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return best


def store_response_and_events(
    store: AuditStore,
    record: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    application_id = clean_text(record.get("application_id"))
    if not application_id:
        raise ValueError("record.application_id is required")

    for synthetic_event in (
        application_submitted_event(record),
        viewed_event_from_record(record),
        status_event_from_record(record),
    ):
        if synthetic_event is not None:
            events.append(synthetic_event)

    # Upsert a baseline first to satisfy response_events FK, then write
    # events. Finally derive response fields from the complete stored history
    # for this application. This makes parser upgrades idempotent: an existing
    # source_event_id can be reclassified without leaving stale funnel flags.
    baseline = dict(record)
    store.upsert_response(baseline)
    for event in events:
        event["application_id"] = application_id
        store.upsert_event(event)

    stored_events = [
        dict(row)
        for row in store.conn.execute(
            """
            SELECT application_id, source_event_id, timestamp, author,
                   event_type, text, raw_json
            FROM response_events
            WHERE application_id = ?
            ORDER BY timestamp, event_id
            """,
            (application_id,),
        ).fetchall()
    ]
    merged = derive_from_events(baseline, stored_events)

    # employer_replied is a substantive-contact flag. Clear stale truthy
    # values from earlier parser versions when the only employer-side event is
    # a generic acknowledgement or bot message.
    has_substantive_reply = any(
        event.get("event_type") in {"employer_message", "employer_invite", "rejection"}
        for event in stored_events
    )
    merged["employer_replied"] = int(has_substantive_reply)
    if not has_substantive_reply:
        merged["first_reply_at"] = None

    store.upsert_response(merged)
    return merged


def current_response(store: AuditStore, application_id: str) -> dict[str, Any]:
    row = store.conn.execute(
        "SELECT * FROM responses WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        return {"application_id": application_id, "negotiation_id": application_id}
    return dict(row)


def discover_page(
    context: BrowserContext,
    page: Page,
    page_number: int,
    *,
    limiter: RateLimiter,
    user_agent: str,
) -> tuple[str, list[dict[str, Any]], int | None]:
    goto_read_only(page, list_page_url(page_number), limiter=limiter)

    records = extract_ssr_list_records(page)
    if records:
        return "hh_ssr", records, None

    api_mode, api_records, api_pages = try_discover_api_page(
        context,
        page_number,
        limiter=limiter,
        user_agent=user_agent,
    )
    if api_records:
        return api_mode, api_records, api_pages

    records = extract_dom_list_records(page)
    if records:
        return "hh_dom", records, None

    return "empty", [], api_pages


def enrich_one(
    context: BrowserContext,
    page: Page,
    record: dict[str, Any],
    *,
    limiter: RateLimiter,
    user_agent: str,
    detail_api_enabled: bool = True,
    chat_topic_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    application_id = clean_text(record.get("application_id"))
    if not application_id:
        return record, [], "missing_application_id"

    status = 0
    payload: Any = None
    if detail_api_enabled:
        detail_api_url = f"{NEGOTIATIONS_API_URL}/{application_id}"
        status, payload = api_get_json(
            context,
            detail_api_url,
            limiter=limiter,
            user_agent=user_agent,
        )
        if status == 200 and isinstance(payload, dict):
            merged = response_from_api_detail(record, payload)
            events = extract_events_from_payload(application_id, payload)
            for event in (
                status_event_from_detail(merged, payload),
                viewed_event_from_detail(merged, payload),
            ):
                if event is not None:
                    events.append(event)
            return derive_from_events(merged, events), events, None

    chatik_status = 0
    chatik_payload: Any = None
    if chat_topic_index is not None:
        chat_entry = chatik_match_entry(record, chat_topic_index)
        if chat_entry is None or not bool(chat_entry.get("has_activity")):
            merged = enrich_from_vacancy_page(
                page,
                record,
                limiter=limiter,
            )
            merged["detail_collected_at"] = now_iso()
            return derive_from_events(merged, []), [], None

        chat_id = clean_text(chat_entry.get("chat_id"))
        if not chat_id:
            merged = enrich_from_vacancy_page(
                page,
                record,
                limiter=limiter,
            )
            merged["detail_collected_at"] = now_iso()
            return derive_from_events(merged, []), [], (
                "chat_index_match_without_chat_id"
            )

        chatik_status, chatik_payload = chatik_get_chat_json(
            context,
            chat_id,
            limiter=limiter,
            user_agent=user_agent,
        )
    else:
        chatik_status, chatik_payload = chatik_get_topic_json(
            context,
            application_id,
            limiter=limiter,
            user_agent=user_agent,
        )
    if chatik_status == 200 and isinstance(chatik_payload, dict):
        merged, events = enrich_from_chatik_payload(
            record,
            chatik_payload,
        )
        merged = enrich_from_vacancy_page(
            page,
            merged,
            limiter=limiter,
        )
        return merged, events, None

    record = enrich_from_vacancy_page(
        page,
        record,
        limiter=limiter,
    )

    detail_url = normalize_url(record.get("chat_negotiation_url"))
    if not detail_url:
        detail_url = web_negotiation_url(application_id)

    if not detail_url:
        return (
            record,
            [],
            f"api_status={status};chatik_status={chatik_status};detail_url_missing",
        )

    try:
        goto_read_only(page, detail_url, limiter=limiter)
    except HHChallengeError:
        raise
    except Exception as exc:
        return record, [], (
            f"api_status={status};chatik_status={chatik_status};"
            f"detail_page={type(exc).__name__}:{exc}"
        )

    state = extract_initial_state(page)
    detail_payload = (
        _find_payload_for_negotiation(state, application_id)
        if state is not None
        else None
    )

    merged = dict(record)
    events: list[dict[str, Any]] = []
    if detail_payload is not None:
        try:
            topic_record = response_from_topic(detail_payload)
            for key, value in topic_record.items():
                if value is not None:
                    merged[key] = value
        except ValueError:
            pass
        events.extend(
            extract_events_from_payload(
                application_id,
                detail_payload,
            )
        )

    events.extend(extract_dom_events(page, application_id))

    try:
        body_text = clean_text(page.locator("body").inner_text(timeout=3000))
    except Exception:
        body_text = ""
    lowered = body_text.lower()

    if any(marker in lowered for marker in VIEW_MARKERS):
        merged["viewed_by_employer"] = 1
    if any(marker in lowered for marker in REJECTION_MARKERS):
        merged["rejected"] = 1
    if any(marker in lowered for marker in INVITE_MARKERS):
        merged["invited"] = 1
        merged["employer_replied"] = 1

    merged["detail_collected_at"] = now_iso()
    error = None
    if detail_payload is None and not events:
        error = (
            f"api_status={status};chatik_status={chatik_status};"
            "detail_page_has_no_safe_history"
        )
    return derive_from_events(merged, events), events, error


def export_csv(store: AuditStore) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    response_fields = [
        "application_id",
        "negotiation_id",
        "vacancy_id",
        "vacancy_title",
        "company",
        "vacancy_url",
        "chat_negotiation_url",
        "applied_at",
        "current_status",
        "viewed_by_employer",
        "viewed_at",
        "employer_replied",
        "first_reply_at",
        "rejected",
        "invited",
        "active_dialog",
        "messages_count",
        "last_message_at",
        "collected_at",
    ]
    response_rows = store.conn.execute(
        f"""
        SELECT {", ".join(response_fields)}
        FROM responses
        ORDER BY COALESCE(applied_at, collected_at) DESC
        """
    ).fetchall()
    with RESPONSES_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=response_fields)
        writer.writeheader()
        for row in response_rows:
            writer.writerow(dict(row))

    event_fields = [
        "application_id",
        "timestamp",
        "author",
        "event_type",
        "text",
    ]
    event_rows = store.conn.execute(
        f"""
        SELECT {", ".join(event_fields)}
        FROM response_events
        ORDER BY application_id, timestamp, event_id
        """
    ).fetchall()
    with EVENTS_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=event_fields)
        writer.writeheader()
        for row in event_rows:
            writer.writerow(dict(row))

    return RESPONSES_CSV_PATH, EVENTS_CSV_PATH


def print_funnel_summary(store: AuditStore) -> None:
    response = store.conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN viewed_by_employer = 1 THEN 1 ELSE 0 END) AS viewed,
            SUM(
                CASE
                    WHEN employer_replied = 1 OR rejected = 1 OR invited = 1
                    THEN 1 ELSE 0
                END
            ) AS employer_actioned,
            SUM(CASE WHEN rejected = 1 THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN invited = 1 THEN 1 ELSE 0 END) AS invited,
            SUM(CASE WHEN active_dialog = 1 THEN 1 ELSE 0 END) AS active_dialogs,
            SUM(CASE WHEN COALESCE(messages_count, 0) > 0 THEN 1 ELSE 0 END) AS with_messages
        FROM responses
        """
    ).fetchone()

    employer_messaged = store.conn.execute(
        """
        SELECT COUNT(DISTINCT application_id)
        FROM response_events
        WHERE event_type = 'employer_message'
        """
    ).fetchone()[0]
    employer_acknowledged = store.conn.execute(
        """
        SELECT COUNT(DISTINCT application_id)
        FROM response_events
        WHERE event_type = 'employer_acknowledgement'
        """
    ).fetchone()[0]
    employer_bot_messaged = store.conn.execute(
        """
        SELECT COUNT(DISTINCT application_id)
        FROM response_events
        WHERE event_type = 'employer_bot_message'
        """
    ).fetchone()[0]

    event_rows = store.conn.execute(
        """
        SELECT
            event_type,
            COUNT(*) AS count,
            COUNT(DISTINCT application_id) AS applications
        FROM response_events
        GROUP BY event_type
        ORDER BY count DESC, event_type
        """
    ).fetchall()

    print()
    print(
        "[FUNNEL] "
        f"total={int(response['total'] or 0)} "
        f"viewed={int(response['viewed'] or 0)} "
        f"employer_actioned={int(response['employer_actioned'] or 0)} "
        f"rejected={int(response['rejected'] or 0)} "
        f"invited={int(response['invited'] or 0)} "
        f"employer_messaged={int(employer_messaged or 0)} "
        f"employer_acknowledged={int(employer_acknowledged or 0)} "
        f"employer_bot_messaged={int(employer_bot_messaged or 0)} "
        f"active_dialogs={int(response['active_dialogs'] or 0)} "
        f"with_messages={int(response['with_messages'] or 0)}"
    )
    if event_rows:
        event_summary = ", ".join(
            (
                f"{row['event_type']}={int(row['count'])}"
                f"({int(row['applications'])} apps)"
            )
            for row in event_rows
        )
        print(f"[EVENTS] {event_summary}")


def print_samples(store: AuditStore, limit: int = 3) -> None:
    rows = store.conn.execute(
        """
        SELECT
            application_id,
            vacancy_id,
            vacancy_title,
            company,
            applied_at,
            current_status,
            viewed_by_employer,
            employer_replied,
            rejected,
            invited,
            messages_count
        FROM responses
        ORDER BY collected_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print("[SAMPLE] Записей пока нет.")
        return
    print()
    print("[SAMPLE] Последние записи:")
    for row in rows:
        print(json.dumps(dict(row), ensure_ascii=False, default=str))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only аудит откликов и приглашений HH."
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--limit",
        type=int,
        help="Прочитать не больше N откликов. По умолчанию 10.",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        help="Пройти все доступные отклики.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Продолжить последний незавершённый аудит.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="После сбора обновить CSV-экспорты.",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Показать окно Chromium для диагностики.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Минимальная задержка между чтениями, сек. По умолчанию {DEFAULT_DELAY_SECONDS:g}.",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit должен быть больше нуля")
    if args.delay < 0:
        parser.error("--delay не может быть отрицательным")
    return args


def _start_or_resume_run(
    store: AuditStore,
    args: argparse.Namespace,
) -> tuple[str, sqlite3.Row]:
    if args.resume:
        existing = store.resumable_run()
        if existing is not None:
            store.update_run(
                existing["run_id"],
                status="running",
                last_error=None,
            )
            return existing["run_id"], store.get_run(existing["run_id"])

    requested_limit = None if args.all else (args.limit or DEFAULT_LIMIT)
    mode = "all" if args.all else "limit"
    run_id = store.create_run(
        mode=mode,
        requested_limit=requested_limit,
    )
    return run_id, store.get_run(run_id)


def run_audit(args: argparse.Namespace) -> int:
    store = AuditStore()
    run_id, run = _start_or_resume_run(store, args)
    limiter = RateLimiter(delay_seconds=max(0.0, args.delay))
    guard = ReadOnlyRequestGuard()

    print("=" * 80)
    print("HH RESPONSE AUDIT")
    print("STRICT READ-ONLY: no click/fill/submit; mutating network requests blocked")
    print(f"DB: {DB_PATH}")
    print(f"Run ID: {run_id}")
    print(
        "Mode: "
        + (
            "ALL"
            if run["requested_limit"] is None
            else f"LIMIT {run['requested_limit']}"
        )
    )
    print("=" * 80)

    try:
        with AgentLock():
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    headless=not args.headful,
                    viewport={"width": 1440, "height": 1000},
                )
                guard.install(context)
                try:
                    page = context.pages[0] if context.pages else context.new_page()

                    goto_read_only(page, RESUMES_URL, limiter=limiter)
                    if not hh_is_authenticated(page):
                        raise RuntimeError(
                            "HH session is not authenticated. "
                            "Run check_hh_session.py / hh_login.py first."
                        )

                    try:
                        user_agent = clean_text(
                            page.evaluate("() => navigator.userAgent")
                        )
                    except Exception:
                        user_agent = "hh-response-audit/1.0"

                    run = store.get_run(run_id)
                    requested_limit = run["requested_limit"]
                    if run["phase"] == "discover":
                        page_number = int(run["next_list_page"] or 0)
                        empty_streak = 0

                        while True:
                            if (
                                requested_limit is not None
                                and store.queued_count(run_id) >= int(requested_limit)
                            ):
                                break

                            print(
                                f"[DISCOVER] page={page_number} "
                                f"queued={store.queued_count(run_id)}"
                            )
                            source_mode, records, known_pages = discover_page(
                                context,
                                page,
                                page_number,
                                limiter=limiter,
                                user_agent=user_agent,
                            )
                            store.update_run(
                                run_id,
                                source_mode=source_mode,
                            )

                            before_count = store.queued_count(run_id)
                            for record in records:
                                if (
                                    requested_limit is not None
                                    and store.queued_count(run_id) >= int(requested_limit)
                                ):
                                    break
                                application_id = clean_text(
                                    record.get("application_id")
                                )
                                if not application_id:
                                    continue
                                store.upsert_response(record)
                                position = store.queued_count(run_id)
                                store.enqueue(
                                    run_id,
                                    position=position,
                                    application_id=application_id,
                                    detail_url=normalize_url(
                                        record.get("chat_negotiation_url")
                                    ),
                                )

                            after_count = store.queued_count(run_id)
                            new_count = after_count - before_count
                            print(
                                f"[DISCOVER] source={source_mode} "
                                f"read={len(records)} new={new_count}"
                            )

                            page_number += 1
                            store.update_run(
                                run_id,
                                next_list_page=page_number,
                            )

                            if known_pages is not None and page_number >= known_pages:
                                break
                            if not records or new_count == 0:
                                empty_streak += 1
                            else:
                                empty_streak = 0
                            if empty_streak >= 1:
                                break

                        store.update_run(
                            run_id,
                            phase="details",
                        )

                    pending = list(store.pending_queue(run_id))

                    chat_topic_index: dict[str, dict[str, Any]] | None = None
                    try:
                        chat_topic_index, chat_index_error = chatik_build_topic_index(
                            context,
                            limiter=limiter,
                            user_agent=user_agent,
                        )
                    except HHChallengeError:
                        raise
                    except Exception as exc:
                        chat_index_error = (
                            f"{type(exc).__name__}:{exc}"
                        )
                        chat_topic_index = None

                    if chat_topic_index is None:
                        print(
                            f"[CHAT INDEX WARN] unavailable: {chat_index_error}"
                        )
                    else:
                        unique_chat_entries = _chat_unique_entries(
                            chat_topic_index
                        )
                        active_topics = sum(
                            1
                            for item in unique_chat_entries
                            if bool(item.get("has_activity"))
                        )
                        print(
                            f"[CHAT INDEX] aliases={len(chat_topic_index)} "
                            f"chats={len(unique_chat_entries)} "
                            f"active={active_topics}"
                        )
                        print_chat_index_diagnostics(
                            store,
                            chat_topic_index,
                        )
                        if chat_index_error:
                            print(f"[CHAT INDEX WARN] {chat_index_error}")

                    detail_api_enabled = False
                    if pending:
                        probe_id = clean_text(pending[0]["application_id"])
                        detail_api_enabled = probe_official_detail_api(
                            context,
                            probe_id,
                            limiter=limiter,
                            user_agent=user_agent,
                        )
                    print(
                        "[DETAIL API] "
                        + ("enabled" if detail_api_enabled else "disabled after probe")
                    )

                    print(f"[DETAILS] pending={len(pending)}")
                    for index, queue_row in enumerate(pending, start=1):
                        application_id = clean_text(queue_row["application_id"])
                        record = current_response(store, application_id)
                        if queue_row["detail_url"]:
                            record["chat_negotiation_url"] = queue_row["detail_url"]

                        print(
                            f"[DETAIL {index}/{len(pending)}] "
                            f"application_id={application_id}"
                        )
                        enriched, events, detail_error = enrich_one(
                            context,
                            page,
                            record,
                            limiter=limiter,
                            user_agent=user_agent,
                            detail_api_enabled=detail_api_enabled,
                            chat_topic_index=chat_topic_index,
                        )
                        if detail_error:
                            print(
                                f"[DETAIL WARN] {application_id}: {detail_error}"
                            )
                        store_response_and_events(
                            store,
                            enriched,
                            events,
                        )
                        store.mark_processed(
                            run_id,
                            int(queue_row["position"]),
                        )

                    store.mark_run_done(
                        run_id,
                        blocked_count=len(guard.blocked),
                    )
                finally:
                    context.close()

        if args.export_csv:
            responses_path, events_path = export_csv(store)
            print(f"[CSV] {responses_path}")
            print(f"[CSV] {events_path}")

        total = int(
            store.conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        )
        events_total = int(
            store.conn.execute("SELECT COUNT(*) FROM response_events").fetchone()[0]
        )
        print()
        print(f"[OK] responses in DB: {total}")
        print(f"[OK] events in DB: {events_total}")
        print(
            "[READ-ONLY] potentially mutating browser requests blocked: "
            f"{len(guard.blocked)}"
        )
        if guard.blocked:
            for item in guard.blocked[:10]:
                print(
                    f"  BLOCKED {item['method']} {item['url'][:220]}"
                )
        print_funnel_summary(store)
        print_samples(store)
        return 0

    except RuntimeError as exc:
        if isinstance(exc, HHChallengeError):
            store.mark_run_failed(run_id, exc)
            print(
                "[SAFE STOP] HH показал captcha/security challenge. "
                "Оставшиеся записи не помечены обработанными."
            )
            print(
                "[SAFE STOP] Пройди проверку вручную в обычном HH-профиле, "
                "затем продолжи: python hh_response_audit.py --resume --export-csv"
            )
            print(f"[SAFE STOP] reason={exc}")
            return 4
        if str(exc) == "agent_lock_busy":
            store.mark_run_failed(
                run_id,
                RuntimeError(
                    "AgentLock занят collector/apply процессом; "
                    "аудит ничего не запускал."
                ),
            )
            print(
                "[SAFE STOP] AgentLock занят. "
                "Collector/apply не прерываю; запусти аудит позже или с --resume."
            )
            return 3
        store.mark_run_failed(run_id, exc)
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 2
    except Exception as exc:
        store.mark_run_failed(run_id, exc)
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
