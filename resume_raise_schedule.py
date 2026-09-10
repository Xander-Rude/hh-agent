from __future__ import annotations

import re
from datetime import datetime, timedelta


RAISE_TIME_RE = re.compile(
    r"поднять(?:\s+резюме)?\s+в\s+(\d{1,2}):(\d{2})",
    re.IGNORECASE,
)

# HH normally schedules the next free raise roughly four hours ahead. If a
# displayed clock time is many hours behind us, it therefore belongs to the
# next calendar day rather than the current one.
ROLLOVER_AFTER_PAST_HOURS = 6


def local_now() -> datetime:
    return datetime.now().astimezone()


def next_raise_at_from_text(
    text: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the earliest free-raise time advertised by HH.

    HH renders values such as ``Поднять в 19:25`` without a date. Times just
    behind the current clock are treated as already due (the CTA can lag while
    the SPA refreshes). Times far behind the current clock are interpreted as
    tomorrow, which covers the midnight rollover case.
    """
    current = now or local_now()
    candidates: list[datetime] = []

    for match in RAISE_TIME_RE.finditer(text or ""):
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            continue

        candidate = current.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        if candidate < current:
            age = current - candidate
            if age > timedelta(hours=ROLLOVER_AFTER_PAST_HOURS):
                candidate += timedelta(days=1)
            else:
                # The advertised time has just arrived/passed but HH has not
                # swapped the text for the free CTA yet. Retry as soon as the
                # lightweight scheduler wakes us again.
                candidate = current

        candidates.append(candidate)

    return min(candidates) if candidates else None


def parse_due_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.astimezone()

    return parsed


def should_run_for_due_at(
    value: object,
    *,
    now: datetime | None = None,
    lead_seconds: int = 30,
) -> bool:
    """Whether the expensive Playwright worker should run now.

    Missing or malformed state intentionally fails open so a corrupted runtime
    state cannot disable resume raising forever.
    """
    due_at = parse_due_at(value)
    if due_at is None:
        return True

    current = now or local_now()
    return due_at <= current + timedelta(seconds=max(0, lead_seconds))


def retry_at(
    minutes: int,
    *,
    now: datetime | None = None,
) -> datetime:
    current = now or local_now()
    return current + timedelta(minutes=max(1, minutes))


def to_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
