"""Fair, freshness-first CLEAN/OLD shadow scheduling without duplicate inference."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Iterable

from app.hard_filters import check_blacklist_company, check_salary


FRESH_HOURS = 24
# At full load: 8 promising OLD, 8 promising CLEAN, 4 rescue/audit.
# Rejected legacy candidates are never permanently dropped just for a score.
_PRIORITY_SLOTS = ("old", "clean", "old", "clean", "audit")


def final_shadow_veto(vacancy, preferences: dict) -> str | None:
    """Safe pre-LLM veto shared by OLD and CLEAN.

    Only a *known upper salary cap* or an explicitly blacklisted employer
    qualifies. A lower-only salary is NOT a cap. A low legacy score, rejected
    title, or hard-filter appeal is not an absolute veto for CLEAN.
    """
    if vacancy.salary_to is not None:
        salary = check_salary(
            salary_from=vacancy.salary_from,
            salary_to=vacancy.salary_to,
            salary_currency=vacancy.salary_currency,
            preferences=preferences,
        )
        if not salary.passed:
            return "known_salary_cap"
    company = check_blacklist_company(
        company=vacancy.company, preferences=preferences,
    )
    if not company.passed:
        return "blacklisted_company"
    return None


def prioritize_shadow_rows(
    rows: list[tuple],
    discoveries: Iterable[tuple[int, str, datetime | None]],
    *,
    now: datetime | None = None,
    old_min_score: int = 80,
) -> list[tuple]:
    """Prioritize plausible candidates; preserve an audit lane for false negatives.

    OLD requires >= min_score. CLEAN can rescue primary false negatives, so
    low-scoring rejects remain queued, with 20% of slots when all lanes are
    backlogged. Shared vacancies are evaluated just once.
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
        # Discovering the same vacancy via a *different source* today does
        # not make an old account-level discovery fresh again.
        if previous is None or first_seen < previous:
            by_vacancy[int(vacancy_id)][account_key] = first_seen

    groups: dict[str, list[tuple]] = {
        "old": [],
        "clean": [],
        "audit": [],
    }
    for row in rows:
        _, vacancy, evaluation = row
        score = int(evaluation.score or 0)
        decision = str(evaluation.decision or "").lower().strip()
        sightings = by_vacancy.get(int(vacancy.id), {})
        plausible = score >= old_min_score or (
            score >= 60 and decision in {"apply", "review"}
        )
        if (
            plausible
            and score >= old_min_score
            and sightings.get("old", datetime.min) >= fresh_cutoff
        ):
            groups["old"].append(row)
        if plausible:
            # The shared "clean" lane also drains older viable OLD candidates
            # so they never sit behind a mountain of low-confidence rejects.
            groups["clean"].append(row)
        else:
            # Audit low-confidence rejects, including formerly hard-filtered
            # records. No DROP without an independently verified final veto.
            groups["audit"].append(row)

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
            for fallback in ("old", "clean", "audit"):
                if fallback != lane and take(fallback):
                    break
        if len(result) == before:
            break

    return result
