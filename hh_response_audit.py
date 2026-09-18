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
    return f"https://hh.ru/applicant/negotiations/{negotiati