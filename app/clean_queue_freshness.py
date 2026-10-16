"""Bound CLEAN semantic re-evaluations to recently first-seen HH vacancies.

HH publication timestamps are currently unpopulated; found_at is the
conservative persisted UTC date we can reliably use without an HH API.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta


CLEAN_QUEUE_LOOKBACK_DAYS = max(
    1, int(os.getenv("CLEAN_QUEUE_LOOKBACK_DAYS", "9"))
)


def clean_queue_cutoff() -> datetime:
    """Rolling UTC threshold as naive UTC, matching SQLite Vacancy.found_at."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=CLEAN_QUEUE_LOOKBACK_DAYS
    )


def is_clean_queue_fresh(found_at: datetime | None) -> bool:
    if found_at is None:
        return False
    normalized = (
        found_at.astimezone(UTC).replace(tzinfo=None)
        if found_at.tzinfo is not None
        else found_at
    )
    return normalized >= clean_queue_cutoff()
