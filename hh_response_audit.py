"""Strict read-only audit of applicant responses on HH.

The script reuses the shared Playwright ``browser-profile`` and never imports
the apply worker. Browser traffic is guarded to GET/HEAD/OPTIONS only. The
documented negotiation message-list endpoint is intentionally not read because
HH may clear ``has_updates`` when that list is viewed.
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
from urllib.parse import parse_qs, urljoin, urlparse

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
    return any(marker in text for marker in TERMINAL_STATE_MARKERS[:2]) or any(
        marker in text for marker in REJECTION_MARKERS
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
    """Abort every browser request whose HTTP method can mutate server state."""

    def __init__(self) -> None:
        self.blocked: list[dict[str, str]] = []

    def install(self, context: BrowserContext) -> None:
        def handle(route, request) -> None:
            method = clean_text(request.method).upper()
            if method in SAFE_HTTP_METHODS:
                route.continue_()
                return
            self.blocked.append(
                {
                    "method": method,
                    "url": clean_text(request.url),
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
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.wait_for_timeout(650)


def extract_dom_list_records(page: Page) -> list[dict[str, Any]]:
    rows = page.evaluate(
        r"""
        () => {
          const absolute = (href) => {
            try { return new URL(href, location.href).href; } catch (_) { return null; }
          };
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          const neg = anchors.filter((a) => {
            const href = absolute(a.getAttribute('href'));
            if (!href) return false;
            return /\/applicant\/negotiations\/[^/?#]+/.test(href)
              || /[?&](negotiation_id|negotiationId|nid|response_id|responseId)=/.test(href);
          });
          const out = [];
          const seen = new Set();
          for (const link of neg) {
            const href = absolute(link.getAttribute('href'));
            if (!href || seen.has(href)) continue;
            let root = link;
            for (let i = 0; i < 8 && root && root.parentElement; i++) {
              root = root.parentElement;
              if (root.querySelector && root.querySelector('a[href*="/vacancy/"]')) break;
            }
            const vacancy = root && root.querySelector
              ? root.querySelector('a[href*="/vacancy/"]')
              : null;
            const employer = root && root.querySelector
              ? root.querySelector('a[href*="/employer/"]')
              : null;
            const statusNode = root && root.querySelector
              ? root.querySelector('[data-qa*="status"], [data-qa*="state"]')
              : null;
            out.push({
              negotiation_url: href,
              vacancy_url: vacancy ? absolute(vacancy.getAttribute('href')) : null,
              vacancy_title: vacancy ? (vacancy.textContent || '').trim() : null,
              company: employer ? (employer.textContent || '').trim() : null,
              status: statusNode ? (statusNode.textContent || '').trim() : null,
              text: root ? (root.innerText || '').trim() : null
            });
            seen.add(href);
          }
          return out;
        }
        """
    )
    records: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return records
    for row in rows:
        if not isinstance(row, dict):
            continue
        negotiation_url = normalize_url(row.get("negotiation_url"))
        negotiation_id = extract_negotiation_id(negotiation_url)
        if not negotiation_id:
            continue
        status = clean_text(row.get("status")) or None
        text = clean_text(row.get("text")).lower()
        records.append(
            {
                "application_id": negotiation_id,
                "negotia