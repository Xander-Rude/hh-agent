from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clean_live_guard import (
    assessment_version_filters,
    clean_eligibility,
    current_policy_context,
)
from app.db import (
    Application,
    CleanShadowAssessment,
    HhVacancyDiscovery,
    SessionLocal,
    Vacancy,
)
from hh_accounts import account_activated_at


DEFAULT_TIMEZONE = os.getenv(
    "CLEAN_FUNNEL_TIMEZONE",
    "Europe/Moscow",
)


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _source_bucket(sources: set[str]) -> str:
    normalized = {
        str(item or "").strip().lower()
        for item in sources
        if str(item or "").strip()
    }
    if "recommendation" in normalized and "search" in normalized:
        return "both"
    if normalized == {"recommendation"}:
        return "recommendation"
    if normalized == {"search"}:
        return "search"
    if len(normalized) == 1:
        return next(iter(normalized))
    if not normalized:
        return "unknown"
    return "+".join(sorted(normalized))


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(
            counter.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    }


def _local_day_start_utc_naive(
    now: datetime | None = None,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    tz = ZoneInfo(timezone_name)
    if now is None:
        local_now = datetime.now(tz)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=UTC).astimezone(tz)
    else:
        local_now = now.astimezone(tz)

    local_start = local_now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return local_start.astimezone(UTC).replace(tzinfo=None)


def _primary_suppression_reason(
    *,
    eligibility_reason: str,
    hard_stops: list[str],
) -> str:
    reason = str(eligibility_reason or "").strip()
    if reason.startswith("hard_stop:"):
        return reason

    if hard_stops:
        return f"hard_stop:{hard_stops[0]}"

    return reason or "suppressed:unknown"


def build_clean_funnel_snapshot(
    session: Session,
    *,
    account_key: str = "clean",
    since: datetime | None = None,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict:
    if since is None:
        since = account_activated_at(account_key)

    discovery_query = select(HhVacancyDiscovery).where(
        HhVacancyDiscovery.account_key == account_key
    )
    if since is not None:
        discovery_query = discovery_query.where(
            HhVacancyDiscovery.first_seen_at >= since
        )

    discoveries = list(session.scalars(discovery_query))
    source_sets: dict[int, set[str]] = defaultdict(set)
    first_seen: dict[int, datetime] = {}

    for row in discoveries:
        vacancy_id = int(row.vacancy_id)
        source_sets[vacancy_id].add(str(row.discovery_source or "unknown"))
        existing = first_seen.get(vacancy_id)
        if existing is None or row.first_seen_at < existing:
            first_seen[vacancy_id] = row.first_seen_at

    discovered_ids = set(source_sets)
    source_bucket = {
        vacancy_id: _source_bucket(sources)
        for vacancy_id, sources in source_sets.items()
    }
    discovered_by_source = Counter(source_bucket.values())

    context = current_policy_context()
    assessments: dict[int, CleanShadowAssessment] = {}
    vacancies: dict[int, Vacancy] = {}

    if discovered_ids:
        latest_assessment_id = (
            select(func.max(CleanShadowAssessment.id))
            .where(
                CleanShadowAssessment.vacancy_id == Vacancy.id,
                *assessment_version_filters(context),
            )
            .correlate(Vacancy)
            .scalar_subquery()
        )
        rows = session.execute(
            select(Vacancy, CleanShadowAssessment)
            .join(
                CleanShadowAssessment,
                CleanShadowAssessment.id == latest_assessment_id,
            )
            .where(Vacancy.id.in_(discovered_ids))
        ).all()

        for vacancy, assessment in rows:
            vacancy_id = int(vacancy.id)
            vacancies[vacancy_id] = vacancy
            assessments[vacancy_id] = assessment

    assessed_ids = set(assessments)
    pending_assessment_ids = discovered_ids - assessed_ids

    routes = Counter()
    routes_by_source: dict[str, Counter] = defaultdict(Counter)
    assessed_by_source = Counter()
    eligible_ids: set[int] = set()
    suppressed_ids: set[int] = set()
    suppressed_reasons = Counter()
    suppressed_reasons_by_source: dict[str, Counter] = defaultdict(Counter)
    suppressed_hard_stops = Counter()

    for vacancy_id, assessment in assessments.items():
        bucket = source_bucket.get(vacancy_id, "unknown")
        assessed_by_source[bucket] += 1

        route = str(assessment.routing_class or "UNKNOWN")
        routes[route] += 1
        routes_by_source[bucket][route] += 1

        eligibility = clean_eligibility(
            session,
            vacancy_id,
            context=context,
        )
        if eligibility.eligible:
            eligible_ids.add(vacancy_id)
            continue

        suppressed_ids.add(vacancy_id)
        hard_stops = _loads_list(assessment.hard_stops)
        reason = _primary_suppression_reason(
            eligibility_reason=eligibility.reason,
            hard_stops=hard_stops,
        )
        suppressed_reasons[reason] += 1
        suppressed_reasons_by_source[bucket][reason] += 1
        for code in hard_stops:
            suppressed_hard_stops[code] += 1

    applications = list(
        session.scalars(
            select(Application).where(
                Application.account_key == account_key,
                Application.vacancy_id.in_(discovered_ids)
                if discovered_ids
                else Application.id == -1,
            )
        )
    )

    application_statuses = Counter()
    surfaced_ids: set[int] = set()
    skipped_ids: set[int] = set()
    applied_ids: set[int] = set()

    for application in applications:
        vacancy_id = int(application.vacancy_id)
        application_statuses[str(application.status or "unknown")] += 1

        if application.telegram_notified_at is not None:
            surfaced_ids.add(vacancy_id)

        if str(application.status or "") == "skipped":
            skipped_ids.add(vacancy_id)

        if (
            str(application.status or "")
            in {"applied", "already_applied"}
            or application.applied_at is not None
        ):
            applied_ids.add(vacancy_id)

    surfaced_by_source = Counter(
        source_bucket.get(vacancy_id, "unknown")
        for vacancy_id in surfaced_ids
    )
    skipped_by_source = Counter(
        source_bucket.get(vacancy_id, "unknown")
        for vacancy_id in skipped_ids
    )
    applied_by_source = Counter(
        source_bucket.get(vacancy_id, "unknown")
        for vacancy_id in applied_ids
    )

    today_start = _local_day_start_utc_naive(
        now,
        timezone_name=timezone_name,
    )
    today_ids = {
        vacancy_id
        for vacancy_id, discovered_at in first_seen.items()
        if discovered_at >= today_start
    }
    today_by_source = Counter(
        source_bucket.get(vacancy_id, "unknown")
        for vacancy_id in today_ids
    )

    # Regression observability for Task 1. These are vacancies that the old
    # global Vacancy.found_at cutoff would have hidden even though CLEAN first
    # discovered them in-scope. They are intentionally NOT suppressed now.
    legacy_global_age_mismatch = 0
    if since is not None and today_ids:
        missing_vacancy_ids = today_ids - set(vacancies)
        if missing_vacancy_ids:
            for vacancy in session.scalars(
                select(Vacancy).where(Vacancy.id.in_(missing_vacancy_ids))
            ):
                vacancies[int(vacancy.id)] = vacancy

        for vacancy_id in today_ids:
            vacancy = vacancies.get(vacancy_id)
            if (
                vacancy is not None
                and vacancy.found_at is not None
                and vacancy.found_at < since
            ):
                legacy_global_age_mismatch += 1

    generated_at = (
        now.astimezone(ZoneInfo(timezone_name))
        if now is not None and now.tzinfo is not None
        else datetime.now(ZoneInfo(timezone_name))
    )

    return {
        "account_key": account_key,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "timezone": timezone_name,
        "discovered": len(discovered_ids),
        "discovered_by_source": _counter_dict(discovered_by_source),
        "assessed": len(assessed_ids),
        "assessed_by_source": _counter_dict(assessed_by_source),
        "pending_assessment": len(pending_assessment_ids),
        "routes": _counter_dict(routes),
        "routes_by_source": {
            source: _counter_dict(counter)
            for source, counter in sorted(routes_by_source.items())
        },
        "eligible": len(eligible_ids),
        "suppressed": len(suppressed_ids),
        "suppressed_by_reason": _counter_dict(suppressed_reasons),
        "suppressed_hard_stop_codes": _counter_dict(
            suppressed_hard_stops
        ),
        "suppressed_by_source": {
            source: _counter_dict(counter)
            for source, counter in sorted(
                suppressed_reasons_by_source.items()
            )
        },
        "surfaced": len(surfaced_ids),
        "surfaced_by_source": _counter_dict(surfaced_by_source),
        "skipped": len(skipped_ids),
        "skipped_by_source": _counter_dict(skipped_by_source),
        "applied": len(applied_ids),
        "applied_by_source": _counter_dict(applied_by_source),
        "application_statuses": _counter_dict(application_statuses),
        "today": {
            "discovered": len(today_ids),
            "discovered_by_source": _counter_dict(today_by_source),
            # Current funnel has no Vacancy.found_at suppression path.
            "global_age_suppressed": 0,
            "legacy_global_age_mismatch": legacy_global_age_mismatch,
        },
    }


def current_clean_funnel_snapshot(
    *,
    account_key: str = "clean",
) -> dict:
    session = SessionLocal()
    try:
        return build_clean_funnel_snapshot(
            session,
            account_key=account_key,
        )
    finally:
        session.close()


def _source_summary(values: dict[str, int]) -> str:
    labels = (
        ("recommendation", "rec"),
        ("search", "search"),
        ("both", "both"),
    )
    parts = [
        f"{label} {int(values.get(key, 0) or 0)}"
        for key, label in labels
        if int(values.get(key, 0) or 0)
    ]
    other = sum(
        int(value or 0)
        for key, value in values.items()
        if key not in {"recommendation", "search", "both"}
    )
    if other:
        parts.append(f"other {other}")
    return " · ".join(parts) if parts else "—"


def format_clean_funnel_lines(snapshot: dict) -> list[str]:
    routes = snapshot.get("routes") or {}
    suppress = snapshot.get("suppressed_by_reason") or {}
    top_suppress = list(suppress.items())[:4]
    today = snapshot.get("today") or {}

    route_parts = []
    for key, label in (
        ("CLEAN_STRONG", "strong"),
        ("CLEAN_REVIEW", "review"),
        ("OLD_REVIEW", "old"),
        ("SKIP", "skip"),
        ("COMPANY_RESERVE", "reserve"),
    ):
        value = int(routes.get(key, 0) or 0)
        if value:
            route_parts.append(f"{label} {value}")

    lines = [
        "CLEAN funnel:",
        (
            f"  discovered: {int(snapshot.get('discovered', 0) or 0)}"
            f" · {_source_summary(snapshot.get('discovered_by_source') or {})}"
        ),
        (
            f"  assessed: {int(snapshot.get('assessed', 0) or 0)}"
            f" · pending {int(snapshot.get('pending_assessment', 0) or 0)}"
            f" · eligible {int(snapshot.get('eligible', 0) or 0)}"
            f" · suppressed {int(snapshot.get('suppressed', 0) or 0)}"
        ),
        "  routes: " + (" · ".join(route_parts) if route_parts else "—"),
        (
            f"  surfaced: {int(snapshot.get('surfaced', 0) or 0)}"
            f" · skipped {int(snapshot.get('skipped', 0) or 0)}"
            f" · applied {int(snapshot.get('applied', 0) or 0)}"
        ),
        (
            f"  today: discovered {int(today.get('discovered', 0) or 0)}"
            f" · age_suppressed "
            f"{int(today.get('global_age_suppressed', 0) or 0)}"
            f" · legacy_age_mismatch "
            f"{int(today.get('legacy_global_age_mismatch', 0) or 0)}"
        ),
    ]

    if top_suppress:
        lines.append(
            "  suppress: "
            + " · ".join(
                f"{reason} {int(count)}"
                for reason, count in top_suppress
            )
        )

    return lines
