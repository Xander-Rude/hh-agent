"""Read-only analytics for HH response audit + hh-agent local history.

This script never opens HH and never mutates either SQLite database. It reads:
- data/hh_response_audit.sqlite
- data/hh_agent.db

Outputs CSV reports under data/analysis/.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
AUDIT_DB_PATH = DATA_DIR / "hh_response_audit.sqlite"
AGENT_DB_PATH = DATA_DIR / "hh_agent.db"
OUTPUT_DIR = DATA_DIR / "analysis"

MERGED_CSV = OUTPUT_DIR / "hh_response_merged.csv"
WEEKLY_CSV = OUTPUT_DIR / "weekly_metrics.csv"
SEGMENTS_CSV = OUTPUT_DIR / "segment_metrics.csv"
SCORES_CSV = OUTPUT_DIR / "score_metrics.csv"
RESUMES_CSV = OUTPUT_DIR / "resume_metrics.csv"
PERIODS_CSV = OUTPUT_DIR / "period_metrics.csv"
HISTORY_CSV = OUTPUT_DIR / "agent_history_summary.csv"


SEGMENT_PMO = "PMO/Program/Portfolio/Project"
SEGMENT_DELIVERY = "Delivery/IT Lead/Engineering"
SEGMENT_PRODUCT = "Product/Platform/CTO"
SEGMENT_OTHER = "Other/Ambiguous"


@dataclass(frozen=True)
class MetricRow:
    total: int
    viewed: int
    rejected: int
    substantive_contact: int
    acknowledged: int
    invited: int
    silent_after_view: int
    not_viewed: int


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def parse_dt(value: Any) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def days_between(start: Any, end: Any) -> float | None:
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() / 86400.0)


def hours_between(start: Any, end: Any) -> float | None:
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    delta = (end_dt - start_dt).total_seconds() / 3600.0
    return delta if delta >= 0 else None


def _contains(text: str, *patterns: str) -> bool:
    return any(pattern in text for pattern in patterns)


def classify_title(title: Any) -> str:
    """Deterministic title-only classification for job-search diagnostics."""
    text = clean_text(title).lower()
    if not text:
        return SEGMENT_OTHER

    # Product/platform/CTO should win over generic "руководитель".
    if _contains(
        text,
        "product owner",
        "product manager",
        "продакт",
        "продукт",
        "владелец продукта",
        "владелец платформ",
        "product lead",
        "head of product",
        "руководитель продукта",
        "руководитель платформ",
        "лидер платформ",
        "platform lead",
        "platform manager",
        "cto",
        "chief technology",
        "технический директор",
        "директор по технологиям",
    ):
        return SEGMENT_PRODUCT

    # Technical/delivery/engineering leadership.
    if _contains(
        text,
        "delivery",
        "delivery manager",
        "head of delivery",
        "it lead",
        "tech lead",
        "technical lead",
        "engineering manager",
        "head of engineering",
        "руководитель разработки",
        "руководитель направления разработки",
        "руководитель ит",
        "руководитель it",
        "ит-лид",
        "it-лид",
        "технический руководитель",
        "технический менеджер",
        "руководитель техничес",
        "руководитель инфраструктур",
        "руководитель devops",
        "devops lead",
        "руководитель эксплуатации",
        "руководитель направления",
    ):
        return SEGMENT_DELIVERY

    # PMO / project / program / portfolio.
    if _contains(
        text,
        "pmo",
        "project manager",
        "senior project manager",
        "program manager",
        "programme manager",
        "portfolio manager",
        "руководитель проектов",
        "руководитель проекта",
        "менеджер проектов",
        "менеджер проекта",
        "ведущий менеджер проектов",
        "директор проектов",
        "руководитель программ",
        "руководитель программы",
        "программный менеджер",
        "руководитель портфел",
        "портфель проектов",
    ):
        return SEGMENT_PMO

    return SEGMENT_OTHER


def score_bucket(score: Any) -> str:
    try:
        value = int(score)
    except (TypeError, ValueError):
        return "No score"
    if value < 72:
        return "<72"
    if value < 80:
        return "72-79"
    if value < 90:
        return "80-89"
    return "90+"


def week_start(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        return "Unknown"
    date = dt.date()
    monday = date.fromordinal(date.toordinal() - date.weekday())
    return monday.isoformat()


def open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        clean_text(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def load_event_flags(audit: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = audit.execute(
        """
        SELECT application_id, event_type, timestamp
        FROM response_events
        ORDER BY application_id, timestamp
        """
    ).fetchall()

    flags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "substantive_contact": 0,
            "acknowledged": 0,
            "bot_messaged": 0,
            "event_invited": 0,
            "first_substantive_contact_at": None,
            "first_action_at": None,
        }
    )

    employer_action_types = {
        "employer_message",
        "employer_invite",
        "rejection",
    }

    for row in rows:
        app_id = clean_text(row["application_id"])
        event_type = clean_text(row["event_type"])
        timestamp = clean_text(row["timestamp"]) or None
        item = flags[app_id]

        if event_type in {"employer_message", "employer_invite"}:
            item["substantive_contact"] = 1
            if timestamp and (
                item["first_substantive_contact_at"] is None
                or parse_dt(timestamp)
                < parse_dt(item["first_substantive_contact_at"])
            ):
                item["first_substantive_contact_at"] = timestamp

        if event_type == "employer_acknowledgement":
            item["acknowledged"] = 1
        if event_type == "employer_bot_message":
            item["bot_messaged"] = 1
        if event_type == "employer_invite":
            item["event_invited"] = 1

        if event_type in employer_action_types and timestamp:
            current = item["first_action_at"]
            if current is None or parse_dt(timestamp) < parse_dt(current):
                item["first_action_at"] = timestamp

    return flags


def load_agent_vacancies(agent: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    columns = table_columns(agent, "vacancies")
    has_source = "source" in columns
    has_external = "external_id" in columns

    select_parts = [
        "id",
        "hh_id",
        "title",
        "company",
        "url",
        "found_at",
    ]
    if has_source:
        select_parts.append("source")
    else:
        select_parts.append("NULL AS source")
    if has_external:
        select_parts.append("external_id")
    else:
        select_parts.append("NULL AS external_id")

    rows = agent.execute(
        f"SELECT {', '.join(select_parts)} FROM vacancies"
    ).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = clean_text(row["source"]).lower()
        hh_id = clean_text(row["hh_id"])
        external_id = clean_text(row["external_id"])

        if source and source != "hh":
            continue
        if hh_id.startswith(("yandex:", "vk:")):
            continue

        key = external_id or hh_id
        if not key:
            continue
        result[key] = dict(row)
    return result


def load_agent_applications(
    agent: sqlite3.Connection,
) -> dict[int, list[dict[str, Any]]]:
    rows = agent.execute(
        """
        SELECT
            id,
            vacancy_id,
            status,
            cover_letter,
            selected_resume_key,
            selected_resume_title,
            selected_resume_id,
            selected_resume_score,
            applied_at,
            created_at
        FROM applications
        ORDER BY vacancy_id, COALESCE(applied_at, created_at), id
        """
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[int(row["vacancy_id"])].append(dict(row))
    return result


def load_agent_evaluations(
    agent: sqlite3.Connection,
) -> dict[int, list[dict[str, Any]]]:
    rows = agent.execute(
        """
        SELECT
            id,
            vacancy_id,
            score,
            decision,
            role_match,
            seniority_match,
            domain_match,
            responsibility_match,
            selected_resume_key,
            selected_resume_title,
            selected_resume_id,
            selected_resume_score,
            created_at
        FROM evaluations
        ORDER BY vacancy_id, created_at, id
        """
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[int(row["vacancy_id"])].append(dict(row))
    return result


def closest_application(
    applications: Iterable[dict[str, Any]],
    response_applied_at: Any,
) -> dict[str, Any] | None:
    candidates = list(applications)
    if not candidates:
        return None

    target = parse_dt(response_applied_at)
    if target is None:
        return candidates[-1]

    def key(item: dict[str, Any]) -> tuple[int, float]:
        value = parse_dt(item.get("applied_at") or item.get("created_at"))
        if value is None:
            return (1, math.inf)
        return (0, abs((value - target).total_seconds()))

    return min(candidates, key=key)


def closest_evaluation(
    evaluations: Iterable[dict[str, Any]],
    application: dict[str, Any] | None,
) -> dict[str, Any] | None:
    candidates = list(evaluations)
    if not candidates:
        return None
    if application is None:
        return candidates[-1]

    target = parse_dt(application.get("created_at") or application.get("applied_at"))
    if target is None:
        return candidates[-1]

    before: list[tuple[datetime, dict[str, Any]]] = []
    all_dated: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        value = parse_dt(item.get("created_at"))
        if value is None:
            continue
        if value <= target:
            before.append((value, item))
        all_dated.append((abs((value - target).total_seconds()), item))

    if before:
        return max(before, key=lambda pair: pair[0])[1]
    if all_dated:
        return min(all_dated, key=lambda pair: pair[0])[1]
    return candidates[-1]


def build_merged_rows(
    audit: sqlite3.Connection,
    agent: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_flags = load_event_flags(audit)
    agent_vacancies = load_agent_vacancies(agent)
    agent_apps = load_agent_applications(agent)
    agent_evals = load_agent_evaluations(agent)

    response_rows = audit.execute(
        """
        SELECT *
        FROM responses
        ORDER BY applied_at, application_id
        """
    ).fetchall()

    snapshot_at = max(
        (
            parse_dt(row["collected_at"])
            for row in response_rows
            if parse_dt(row["collected_at"]) is not None
        ),
        default=datetime.now(timezone.utc),
    )

    merged: list[dict[str, Any]] = []
    matched_to_agent = 0

    for row in response_rows:
        base = dict(row)
        application_id = clean_text(base.get("application_id"))
        vacancy_id = clean_text(base.get("vacancy_id"))
        flags = event_flags.get(application_id, {})

        agent_vacancy = agent_vacancies.get(vacancy_id)
        agent_application = None
        evaluation = None
        if agent_vacancy is not None:
            internal_vacancy_id = int(agent_vacancy["id"])
            agent_application = closest_application(
                agent_apps.get(internal_vacancy_id, []),
                base.get("applied_at"),
            )
            evaluation = closest_evaluation(
                agent_evals.get(internal_vacancy_id, []),
                agent_application,
            )
            matched_to_agent += 1

        title = (
            clean_text(base.get("vacancy_title"))
            or clean_text((agent_vacancy or {}).get("title"))
            or None
        )
        company = (
            clean_text(base.get("company"))
            or clean_text((agent_vacancy or {}).get("company"))
            or None
        )

        viewed = int(bool(base.get("viewed_by_employer")))
        rejected = int(bool(base.get("rejected")))
        invited = int(
            bool(base.get("invited"))
            or bool(flags.get("event_invited"))
        )
        substantive_contact = int(bool(flags.get("substantive_contact")))
        acknowledged = int(bool(flags.get("acknowledged")))
        bot_messaged = int(bool(flags.get("bot_messaged")))
        silent_after_view = int(
            viewed
            and not rejected
            and not invited
            and not substantive_contact
        )

        applied_at = base.get("applied_at")
        age_days = days_between(applied_at, snapshot_at.isoformat())

        first_action_at = flags.get("first_action_at")
        first_contact_at = flags.get("first_substantive_contact_at")
        time_to_first_action_hours = hours_between(applied_at, first_action_at)
        time_to_contact_hours = hours_between(applied_at, first_contact_at)
        time_to_view_hours = hours_between(applied_at, base.get("viewed_at"))

        cover_letter = clean_text(
            (agent_application or {}).get("cover_letter")
        )
        resume_title = clean_text(
            (agent_application or {}).get("selected_resume_title")
            or (evaluation or {}).get("selected_resume_title")
        )
        resume_id = clean_text(
            (agent_application or {}).get("selected_resume_id")
            or (evaluation or {}).get("selected_resume_id")
        )

        score = (
            (evaluation or {}).get("score")
            if evaluation is not None
            else None
        )

        merged.append(
            {
                "application_id": application_id,
                "vacancy_id": vacancy_id,
                "vacancy_title": title,
                "company": company,
                "applied_at": applied_at,
                "week_start": week_start(applied_at),
                "age_days": round(age_days, 2) if age_days is not None else None,
                "mature_3d": int(age_days is not None and age_days >= 3),
                "mature_7d": int(age_days is not None and age_days >= 7),
                "viewed": viewed,
                "rejected": rejected,
                "invited": invited,
                "substantive_contact": substantive_contact,
                "acknowledged": acknowledged,
                "bot_messaged": bot_messaged,
                "silent_after_view": silent_after_view,
                "time_to_view_hours": (
                    round(time_to_view_hours, 2)
                    if time_to_view_hours is not None
                    else None
                ),
                "time_to_first_action_hours": (
                    round(time_to_first_action_hours, 2)
                    if time_to_first_action_hours is not None
                    else None
                ),
                "time_to_contact_hours": (
                    round(time_to_contact_hours, 2)
                    if time_to_contact_hours is not None
                    else None
                ),
                "segment": classify_title(title),
                "agent_matched": int(agent_vacancy is not None),
                "agent_application_id": (
                    (agent_application or {}).get("id")
                ),
                "agent_application_status": (
                    (agent_application or {}).get("status")
                ),
                "agent_applied_at": (
                    (agent_application or {}).get("applied_at")
                ),
                "has_cover_letter": int(bool(cover_letter))
                if agent_application is not None
                else None,
                "selected_resume_title": resume_title or None,
                "selected_resume_id": resume_id or None,
                "evaluation_score": score,
                "score_bucket": score_bucket(score),
                "evaluation_decision": (
                    (evaluation or {}).get("decision")
                ),
                "role_match": (evaluation or {}).get("role_match"),
                "seniority_match": (evaluation or {}).get("seniority_match"),
                "domain_match": (evaluation or {}).get("domain_match"),
                "responsibility_match": (
                    (evaluation or {}).get("responsibility_match")
                ),
            }
        )

    meta = {
        "snapshot_at": snapshot_at,
        "matched_to_agent": matched_to_agent,
        "total_responses": len(merged),
    }
    return merged, meta


def metric_row(rows: Iterable[dict[str, Any]]) -> MetricRow:
    values = list(rows)
    return MetricRow(
        total=len(values),
        viewed=sum(int(bool(r.get("viewed"))) for r in values),
        rejected=sum(int(bool(r.get("rejected"))) for r in values),
        substantive_contact=sum(
            int(bool(r.get("substantive_contact"))) for r in values
        ),
        acknowledged=sum(int(bool(r.get("acknowledged"))) for r in values),
        invited=sum(int(bool(r.get("invited"))) for r in values),
        silent_after_view=sum(
            int(bool(r.get("silent_after_view"))) for r in values
        ),
        not_viewed=sum(not bool(r.get("viewed")) for r in values),
    )


def metrics_dict(label: str, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    metric = metric_row(rows)
    return {
        "group": label,
        "total": metric.total,
        "viewed": metric.viewed,
        "view_rate_pct": percent(metric.viewed, metric.total),
        "rejected": metric.rejected,
        "rejection_rate_pct": percent(metric.rejected, metric.total),
        "rejection_after_view_pct": percent(metric.rejected, metric.viewed),
        "substantive_contact": metric.substantive_contact,
        "contact_rate_pct": percent(metric.substantive_contact, metric.total),
        "contact_after_view_pct": percent(
            metric.substantive_contact, metric.viewed
        ),
        "acknowledged": metric.acknowledged,
        "invited": metric.invited,
        "silent_after_view": metric.silent_after_view,
        "silent_after_view_pct": percent(
            metric.silent_after_view, metric.viewed
        ),
        "not_viewed": metric.not_viewed,
    }


def grouped_metrics(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[clean_text(row.get(key)) or "Unknown"].append(row)
    return [
        metrics_dict(label, values)
        for label, values in sorted(groups.items(), key=lambda item: item[0])
    ]


def latest_agent_first_apply(
    agent: sqlite3.Connection,
) -> datetime | None:
    vacancy_columns = table_columns(agent, "vacancies")
    source_filter = ""
    if "source" in vacancy_columns and "external_id" in vacancy_columns:
        source_filter = "AND (v.source = 'hh' OR v.source IS NULL)"
    else:
        source_filter = "AND v.hh_id NOT LIKE '%:%'"

    row = agent.execute(
        f"""
        SELECT MIN(a.applied_at) AS first_applied_at
        FROM applications a
        JOIN vacancies v ON v.id = a.vacancy_id
        WHERE a.applied_at IS NOT NULL
        {source_filter}
        """
    ).fetchone()
    return parse_dt(row["first_applied_at"]) if row else None


def period_metrics(
    rows: list[dict[str, Any]],
    boundary: datetime | None,
) -> list[dict[str, Any]]:
    if boundary is None:
        return []

    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for row in rows:
        applied = parse_dt(row.get("applied_at"))
        if applied is None:
            continue
        if applied < boundary:
            before.append(row)
        else:
            after.append(row)

    result = [
        metrics_dict(f"before_agent_first_apply<{boundary.isoformat()}", before),
        metrics_dict(f"after_agent_first_apply>={boundary.isoformat()}", after),
    ]
    return result


def agent_history_summary(
    agent: sqlite3.Connection,
) -> list[dict[str, Any]]:
    vacancy_columns = table_columns(agent, "vacancies")
    if "source" in vacancy_columns and "external_id" in vacancy_columns:
        source_expr = "COALESCE(v.source, CASE WHEN v.hh_id LIKE '%:%' THEN 'other' ELSE 'hh' END)"
    else:
        source_expr = "CASE WHEN v.hh_id LIKE '%:%' THEN substr(v.hh_id, 1, instr(v.hh_id, ':') - 1) ELSE 'hh' END"

    rows = agent.execute(
        f"""
        SELECT
            {source_expr} AS source,
            COUNT(*) AS application_records,
            SUM(CASE WHEN a.applied_at IS NOT NULL THEN 1 ELSE 0 END) AS with_applied_at,
            MIN(a.applied_at) AS first_applied_at,
            MAX(a.applied_at) AS last_applied_at,
            SUM(CASE WHEN a.status = 'applied' THEN 1 ELSE 0 END) AS status_applied,
            SUM(CASE WHEN a.status = 'manual_required' THEN 1 ELSE 0 END) AS status_manual_required,
            SUM(CASE WHEN a.status = 'approved' THEN 1 ELSE 0 END) AS status_approved
        FROM applications a
        JOIN vacancies v ON v.id = a.vacancy_id
        GROUP BY {source_expr}
        ORDER BY source
        """
    ).fetchall()
    return [dict(row) for row in rows]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def median(values: Iterable[Any]) -> float | None:
    numeric = sorted(
        float(value)
        for value in values
        if value is not None
    )
    if not numeric:
        return None
    mid = len(numeric) // 2
    if len(numeric) % 2:
        return round(numeric[mid], 2)
    return round((numeric[mid - 1] + numeric[mid]) / 2.0, 2)


def print_metrics_table(title: str, rows: list[dict[str, Any]]) -> None:
    print()
    print(title)
    print("-" * len(title))
    if not rows:
        print("No data")
        return

    headers = [
        "group",
        "total",
        "view_rate_pct",
        "rejection_after_view_pct",
        "contact_after_view_pct",
        "silent_after_view_pct",
    ]
    widths = {
        key: max(
            len(key),
            *[
                len(clean_text(row.get(key)))
                for row in rows
            ],
        )
        for key in headers
    }

    def render(row: dict[str, Any]) -> str:
        return " | ".join(
            clean_text(row.get(key)).ljust(widths[key])
            for key in headers
        )

    print(render({key: key for key in headers}))
    print("-+-".join("-" * widths[key] for key in headers))
    for row in rows:
        print(render(row))


def print_summary(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    first_agent_apply: datetime | None,
    history: list[dict[str, Any]],
) -> None:
    overall = metrics_dict("all", rows)
    mature7 = metrics_dict(
        "mature_7d",
        [row for row in rows if row.get("mature_7d")],
    )

    print("=" * 96)
    print("HH RESPONSE ANALYSIS")
    print(f"Responses: {overall['total']}")
    print(
        f"Matched to hh_agent.db by HH vacancy id: "
        f"{meta['matched_to_agent']}/{meta['total_responses']}"
    )
    print(f"Snapshot: {iso_or_none(meta['snapshot_at'])}")
    print(
        "Funnel: "
        f"viewed={overall['viewed']} ({overall['view_rate_pct']}%), "
        f"rejected={overall['rejected']}, "
        f"substantive_contact={overall['substantive_contact']}, "
        f"invited={overall['invited']}, "
        f"acknowledged={overall['acknowledged']}, "
        f"silent_after_view={overall['silent_after_view']} "
        f"({overall['silent_after_view_pct']}% of viewed)"
    )
    print(
        "Mature >=7d: "
        f"n={mature7['total']} "
        f"view_rate={mature7['view_rate_pct']}% "
        f"silent_after_view={mature7['silent_after_view_pct']}%"
    )

    view_latency = median(
        row.get("time_to_view_hours")
        for row in rows
        if row.get("time_to_view_hours") is not None
    )
    action_latency = median(
        row.get("time_to_first_action_hours")
        for row in rows
        if row.get("time_to_first_action_hours") is not None
    )
    view_latency_n = sum(
        row.get("time_to_view_hours") is not None for row in rows
    )
    action_latency_n = sum(
        row.get("time_to_first_action_hours") is not None for row in rows
    )
    print(
        "Latency coverage: "
        f"view timestamps={view_latency_n}/{len(rows)} "
        f"(median={view_latency}h), "
        f"first employer action timestamps={action_latency_n}/{len(rows)} "
        f"(median={action_latency}h)"
    )

    if first_agent_apply is not None:
        print(f"First confirmed HH application in agent DB: {first_agent_apply.isoformat()}")
    else:
        print("First confirmed HH application in agent DB: unavailable")

    print()
    print("Local agent application history:")
    for row in history:
        print(
            f"  source={row.get('source')} "
            f"records={row.get('application_records')} "
            f"with_applied_at={row.get('with_applied_at')} "
            f"first={row.get('first_applied_at')} "
            f"last={row.get('last_applied_at')}"
        )


def run(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit_db)
    agent_path = Path(args.agent_db)
    output_dir = Path(args.output_dir)

    audit = open_ro(audit_path)
    agent = open_ro(agent_path)
    try:
        rows, meta = build_merged_rows(audit, agent)
        first_agent_apply = latest_agent_first_apply(agent)
        history = agent_history_summary(agent)

        weekly = grouped_metrics(rows, "week_start")
        segments = grouped_metrics(rows, "segment")
        scores = grouped_metrics(
            [row for row in rows if row.get("agent_matched")],
            "score_bucket",
        )
        resumes = grouped_metrics(
            [
                row
                for row in rows
                if row.get("agent_matched")
                and row.get("selected_resume_title")
            ],
            "selected_resume_title",
        )
        periods = period_metrics(rows, first_agent_apply)

        global MERGED_CSV, WEEKLY_CSV, SEGMENTS_CSV, SCORES_CSV
        global RESUMES_CSV, PERIODS_CSV, HISTORY_CSV
        MERGED_CSV = output_dir / "hh_response_merged.csv"
        WEEKLY_CSV = output_dir / "weekly_metrics.csv"
        SEGMENTS_CSV = output_dir / "segment_metrics.csv"
        SCORES_CSV = output_dir / "score_metrics.csv"
        RESUMES_CSV = output_dir / "resume_metrics.csv"
        PERIODS_CSV = output_dir / "period_metrics.csv"
        HISTORY_CSV = output_dir / "agent_history_summary.csv"

        write_csv(MERGED_CSV, rows)
        write_csv(WEEKLY_CSV, weekly)
        write_csv(SEGMENTS_CSV, segments)
        write_csv(SCORES_CSV, scores)
        write_csv(RESUMES_CSV, resumes)
        write_csv(PERIODS_CSV, periods)
        write_csv(HISTORY_CSV, history)

        print_summary(rows, meta, first_agent_apply, history)
        print_metrics_table("WEEKLY", weekly)
        print_metrics_table("SEGMENTS", segments)
        print_metrics_table("SCORE BUCKETS (agent-matched only)", scores)
        print_metrics_table("PERIODS", periods)

        print()
        print("CSV:")
        for path in (
            MERGED_CSV,
            WEEKLY_CSV,
            SEGMENTS_CSV,
            SCORES_CSV,
            RESUMES_CSV,
            PERIODS_CSV,
            HISTORY_CSV,
        ):
            print(f"  {path}")

        # Historical ~648 sanity check: local DB can confirm only applications
        # actually recorded by the agent, not deleted pre-agent HH history.
        hh_history = next(
            (
                row
                for row in history
                if clean_text(row.get("source")).lower() == "hh"
            ),
            None,
        )
        if hh_history is not None:
            confirmed = int(hh_history.get("with_applied_at") or 0)
            print()
            print(
                "[HISTORY] Locally confirmed HH applications with applied_at: "
                f"{confirmed}. This is a lower bound for agent-recorded history, "
                "not proof of the full historical ~648 HH applications."
            )
        return 0
    finally:
        audit.close()
        agent.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only analytics for HH response audit and hh-agent DB."
    )
    parser.add_argument(
        "--audit-db",
        default=str(AUDIT_DB_PATH),
        help=f"Audit SQLite path. Default: {AUDIT_DB_PATH}",
    )
    parser.add_argument(
        "--agent-db",
        default=str(AGENT_DB_PATH),
        help=f"Agent SQLite path. Default: {AGENT_DB_PATH}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help=f"CSV output directory. Default: {OUTPUT_DIR}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
