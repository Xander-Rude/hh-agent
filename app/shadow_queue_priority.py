"""Fair, freshness-first CLEAN/OLD shadow scheduling without duplicate inference."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Iterable


FRESH_HOURS = 24
# At full load: 8 fresh OLD, 8 fresh CLEAN, 4 backlog per 20.
_PRIORITY_SLOTS = ("old", "clean", "old", "clean", "backlog")


def prioritize_shadow_rows(
    rows: list[tuple],
    discoveries: Iterable[tuple[int, str, datetime | None]],
    *,
    now: datetime | None = None,
    old_min_score: int = 80,
) -> list[tuple]:
    """Order once for both accounts. Fresh candidates win; backlog never starves.

    Rows contain (CleanLiveQueue, Vacancy, latest Evaluation). Discoveries are
    per-account *first* sightings, not rediscovery timestamps. A shared vacancy
    can be in both fresh lanes, but its shadow inference is scheduled only once.
    Retry attempts are deferred within each lane rather than dropped.
    """
    if not rows:
        return []

    now = now or datetime.now(UTC).replace(tzinfo=None)
    fresh_cutoff = now - timedelta(hours=FRESH_HOURS)

    by_vacancy: dict[int, dict[str, datetime]] = defaultdict(dict)
    for vacancy_id, account_key, first_seen in discoveries:
        if account_key not in {"old", "clean"} or first_seen is None:
            continue
        previous = by_vacancy[int(vacancy_id)].get(account_key)
        if previous is None or first_seen > previous:
            by_vacancy[int(vacancy_id)][account_key] = first_seen

    groups: dict[str, list[tuple]] = {
        "old": [],
        "clean": [],
        "backlog": [],
    }
    for row in rows:
        queue_row, vacancy, evaluation = row
        found = vacancy.found_at or datetime.min
        sightings = by_vacancy.get(int(vacancy.id), {})
        newest = max((found, *sightings.values()))
        is_new = newest >= fresh_cutoff
        old_fresh = (
            is_new
            and int(evaluation.score or 0) >= old_min_score
            and (
                sightings.get("old", datetime.min) >= fresh_cutoff
                or found >= fresh_cutoff and "old" in sightings
            )
        )
        clean_fresh = (
            is_new
            and (
                sightings.get("clean", datetime.min) >= fresh_cutoff
                or found >= fresh_cutoff and "clean" in sightings
            )
        )
        if old_fresh:
            groups["old"].append(row)
        if clean_fresh:
            groups["clean"].append(row)
        if not (old_fresh or clean_fresh):
            groups["backlog"].append(row)

    def sort_key(row: tuple) -> tuple:
        queue_row, vacancy, _ = row
        sightings = by_vacancy.get(int(vacancy.id), {})
        newest = max((vacancy.found_at or datetime.min, *sightings.values()))
        return (
            int(queue_row.attempts or 0),
            -newest.timestamp() if newest.year > 1970 else 0,
            -int(queue_row.id),
        )

    for group in groups.values():
        group.sort(key=sort_key)

    seen: set[int] = set()
    result: list[tuple] = []
    positions = {name: 0 for name in groups}

    def take(group_name: str) -> bool:
        group = groups[group_name]
        while positions[group_name] < len(group):
            row = group[positions[group_name]]
            positions[group_name] += 1
            vacancy_id = int(row[1].id)
            if vacancy_id in seen:
                continue
            seen.add(vacancy_id)
            result.append(row)
            return True
        return False

    while len(result) < len(rows):
        before = len(result)
        for lane in _PRIORITY_SLOTS:
            if len(result) == len(rows):
                break
            if take(lane):
                continue
            # Unused capacity is not wasted if one account has no fresh work.
            for fallback in ("old", "clean", "backlog"):
                if fallback != lane and take(fallback):
                    break
        if len(result) == before:
            break

    return result
