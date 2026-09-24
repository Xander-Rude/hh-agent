from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import func, select

from app.clean_shadow import (
    COMPANY_POLICY_VERSION,
    GATE_VERSION,
    PROMPT_VERSION,
    ROUTING_VERSION,
    SCORING_VERSION,
    CleanShadowEvaluator,
    _salary_stop,
    build_shadow_scores,
    normalize_company_key,
)
from app.db import (
    Application,
    CleanRescoreItem,
    CleanRescoreRun,
    Evaluation,
    SessionLocal,
    Vacancy,
)
from app.strategy_memory import get_active_memory


RESCORE_VERSION = "clean-rescore-v1"
CLEAN_ROUTES = {"CLEAN_STRONG", "CLEAN_REVIEW"}
OPEN_RUN_STATUSES = {"running", "needs_retry"}
ACTIVE_CLEAN_APPLICATION_STATUSES = {
    "approved",
    "applying",
    "applied",
    "manual_required",
    "already_applied",
}


@dataclass(frozen=True)
class BatchResult:
    run_id: int
    selected_count: int
    processed_count: int
    ok_count: int
    error_count: int
    clean_candidate_count: int
    status: str
    batch_processed: int
    batch_failed: int
    budget_exhausted: bool


AvailabilityProbe = Callable[[dict], str]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(data) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _active_strategy_memory() -> tuple[list[dict], str]:
    memory = get_active_memory()
    if not memory:
        raise RuntimeError("No active strategy memory baseline exists.")

    version = memory.get("version") or {}
    version_number = version.get("version_number")
    content_hash = str(version.get("content_hash") or "").strip()
    if version_number is None or not content_hash:
        raise RuntimeError(
            "Active strategy memory has incomplete version metadata."
        )

    token = (
        f"strategy-memory-v1:{int(version_number)}:"
        f"{content_hash[:16]}"
    )
    return list(memory.get("learned_patterns") or []), token


def _vacancy_snapshot(vacancy: Vacancy) -> dict:
    return {
        "vacancy_id": vacancy.id,
        "hh_id": str(vacancy.external_id or vacancy.hh_id or ""),
        "source": vacancy.source,
        "title": vacancy.title or "",
        "company": vacancy.company or "",
        "url": vacancy.url or "",
        "salary_from": vacancy.salary_from,
        "salary_to": vacancy.salary_to,
        "salary_currency": vacancy.salary_currency,
        "description": vacancy.description or "",
        "published_at": (
            vacancy.published_at.isoformat()
            if vacancy.published_at is not None
            else None
        ),
        "found_at": (
            vacancy.found_at.isoformat()
            if vacancy.found_at is not None
            else None
        ),
    }


def _vacancy_text(snapshot: dict) -> str:
    salary = []
    if snapshot.get("salary_from") is not None:
        salary.append(f"from {snapshot['salary_from']}")
    if snapshot.get("salary_to") is not None:
        salary.append(f"to {snapshot['salary_to']}")
    if snapshot.get("salary_currency"):
        salary.append(str(snapshot["salary_currency"]))

    return "\n".join(
        [
            f"Title: {snapshot.get('title') or ''}",
            f"Company: {snapshot.get('company') or ''}",
            f"Salary: {' '.join(salary) if salary else 'not specified'}",
            f"URL: {snapshot.get('url') or ''}",
            "",
            str(snapshot.get("description") or ""),
        ]
    ).strip()


def _dataset_hash(items: list[tuple[int, int | None, dict]]) -> str:
    digest = hashlib.sha256()
    for vacancy_id, evaluation_id, snapshot in items:
        digest.update(
            _json(
                {
                    "vacancy_id": vacancy_id,
                    "legacy_evaluation_id": evaluation_id,
                    "vacancy": snapshot,
                }
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _open_run(
    session,
    *,
    candidate_profile_version: str,
    recruiter_resume_version: str,
    learned_patterns_version: str,
) -> CleanRescoreRun | None:
    return session.scalar(
        select(CleanRescoreRun)
        .where(
            CleanRescoreRun.status.in_(OPEN_RUN_STATUSES),
            CleanRescoreRun.rescore_version == RESCORE_VERSION,
            CleanRescoreRun.candidate_profile_version
            == candidate_profile_version,
            CleanRescoreRun.recruiter_resume_version
            == recruiter_resume_version,
            CleanRescoreRun.learned_patterns_version
            == learned_patterns_version,
            CleanRescoreRun.prompt_version == PROMPT_VERSION,
            CleanRescoreRun.scoring_version == SCORING_VERSION,
            CleanRescoreRun.gate_version == GATE_VERSION,
            CleanRescoreRun.routing_version == ROUTING_VERSION,
            CleanRescoreRun.company_policy_version
            == COMPANY_POLICY_VERSION,
        )
        .order_by(CleanRescoreRun.id.desc())
        .limit(1)
    )


def create_rescore_run(
    *,
    candidate_profile_version: str,
    recruiter_resume_version: str,
    window_days: int = 7,
    now: datetime | None = None,
    note: str | None = None,
    reuse_open: bool = True,
) -> CleanRescoreRun:
    window_days = max(1, int(window_days))
    _, memory_token = _active_strategy_memory()
    window_to = now or _now()
    window_from = window_to - timedelta(days=window_days)

    session = SessionLocal()
    try:
        if reuse_open:
            existing = _open_run(
                session,
                candidate_profile_version=candidate_profile_version,
                recruiter_resume_version=recruiter_resume_version,
                learned_patterns_version=memory_token,
            )
            if existing is not None:
                session.expunge(existing)
                return existing

        vacancies = session.scalars(
            select(Vacancy)
            .where(
                Vacancy.source == "hh",
                Vacancy.found_at >= window_from,
                Vacancy.found_at <= window_to,
            )
            .order_by(Vacancy.found_at.desc(), Vacancy.id.desc())
        ).all()
        latest_evaluations = {
            int(vacancy_id): int(evaluation_id)
            for vacancy_id, evaluation_id in session.execute(
                select(
                    Evaluation.vacancy_id,
                    func.max(Evaluation.id),
                )
                .join(Vacancy, Vacancy.id == Evaluation.vacancy_id)
                .where(
                    Vacancy.source == "hh",
                    Vacancy.found_at >= window_from,
                    Vacancy.found_at <= window_to,
                )
                .group_by(Evaluation.vacancy_id)
            ).all()
        }

        snapshot_items = [
            (
                vacancy.id,
                latest_evaluations.get(vacancy.id),
                _vacancy_snapshot(vacancy),
            )
            for vacancy in vacancies
        ]
        run = CleanRescoreRun(
            status="running",
            source="hh",
            window_from=window_from,
            window_to=window_to,
            window_days=window_days,
            dataset_hash=_dataset_hash(snapshot_items),
            selected_count=len(snapshot_items),
            processed_count=0,
            ok_count=0,
            error_count=0,
            clean_candidate_count=0,
            rescore_version=RESCORE_VERSION,
            candidate_profile_version=candidate_profile_version,
            recruiter_resume_version=recruiter_resume_version,
            learned_patterns_version=memory_token,
            prompt_version=PROMPT_VERSION,
            scoring_version=SCORING_VERSION,
            gate_version=GATE_VERSION,
            routing_version=ROUTING_VERSION,
            company_policy_version=COMPANY_POLICY_VERSION,
            note=(note.strip() if note else None),
        )
        session.add(run)
        session.flush()

        for vacancy_id, evaluation_id, snapshot in snapshot_items:
            session.add(
                CleanRescoreItem(
                    run_id=run.id,
                    vacancy_id=vacancy_id,
                    legacy_evaluation_id=evaluation_id,
                    status="pending",
                    availability_status="not_checked",
                    vacancy_snapshot=_json(snapshot),
                    extraction_json="{}",
                )
            )

        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run
    finally:
        session.close()


def retry_errors(run_id: int) -> int:
    session = SessionLocal()
    try:
        run = session.get(CleanRescoreRun, int(run_id))
        if run is None:
            raise ValueError(f"clean rescore run not found: {run_id}")
        if run.status not in OPEN_RUN_STATUSES:
            raise RuntimeError(
                f"clean rescore run {run.id} is {run.status}, not resumable"
            )

        rows = session.scalars(
            select(CleanRescoreItem).where(
                CleanRescoreItem.run_id == int(run_id),
                CleanRescoreItem.status == "error",
            )
        ).all()
        for item in rows:
            item.status = "pending"
            item.error = None
            item.updated_at = _now()

        if rows:
            run.status = "running"
            run.completed_at = None

        session.commit()
        return len(rows)
    finally:
        session.close()


def _validate_run_versions(
    run: CleanRescoreRun,
    *,
    candidate_profile_version: str,
    recruiter_resume_version: str,
) -> list[dict]:
    patterns, memory_token = _active_strategy_memory()
    expected = {
        "rescore_version": RESCORE_VERSION,
        "candidate_profile_version": candidate_profile_version,
        "recruiter_resume_version": recruiter_resume_version,
        "learned_patterns_version": memory_token,
        "prompt_version": PROMPT_VERSION,
        "scoring_version": SCORING_VERSION,
        "gate_version": GATE_VERSION,
        "routing_version": ROUTING_VERSION,
        "company_policy_version": COMPANY_POLICY_VERSION,
    }
    mismatches = [
        f"{field}: run={getattr(run, field)!r} current={value!r}"
        for field, value in expected.items()
        if getattr(run, field) != value
    ]
    if mismatches:
        raise RuntimeError(
            "CLEAN rescore version drift; refusing mixed-version run: "
            + "; ".join(mismatches)
        )
    return patterns


def _active_clean_by_company(session) -> dict[str, set[int]]:
    rows = session.execute(
        select(Application, Vacancy)
        .join(Vacancy, Vacancy.id == Application.vacancy_id)
        .where(
            Application.account_key == "clean",
            Application.status.in_(ACTIVE_CLEAN_APPLICATION_STATUSES),
            Vacancy.source == "hh",
        )
    ).all()

    result: dict[str, set[int]] = {}
    for application, vacancy in rows:
        key = normalize_company_key(vacancy.company)
        if key:
            result.setdefault(key, set()).add(vacancy.id)
    return result


def _availability_route(
    base_routing_class: str,
    availability_status: str,
) -> tuple[str, str | None]:
    if base_routing_class not in CLEAN_ROUTES:
        return base_routing_class, None
    if availability_status == "active":
        return base_routing_class, None
    if availability_status == "closed":
        return "SKIP", "vacancy_closed"
    if availability_status == "unresolved":
        return "REVIEW", "availability_unresolved"
    return "REVIEW", "availability_not_checked"


def _rank_companies(run_id: int) -> None:
    session = SessionLocal()
    try:
        rows = session.scalars(
            select(CleanRescoreItem)
            .where(
                CleanRescoreItem.run_id == int(run_id),
                CleanRescoreItem.status == "ok",
            )
            .order_by(CleanRescoreItem.id)
        ).all()
        active_clean = _active_clean_by_company(session)
        groups: dict[str, list[CleanRescoreItem]] = {}

        for item in rows:
            item.company_rank = None
            item.company_state = None
            base = item.base_routing_class or "SKIP"
            route, reason = _availability_route(
                base,
                item.availability_status,
            )
            item.routing_class = route

            reasons = json.loads(item.route_reason_codes or "[]")
            reasons = [
                value
                for value in reasons
                if value not in {
                    "vacancy_closed",
                    "availability_unresolved",
                    "availability_not_checked",
                    "company_reserve",
                    "active_clean_company",
                }
            ]
            if reason:
                reasons.append(reason)
            item.route_reason_codes = _json(reasons)

            if (
                item.status == "ok"
                and base in CLEAN_ROUTES
                and item.availability_status == "active"
                and item.company_entity_key
            ):
                groups.setdefault(item.company_entity_key, []).append(item)

        for key, items in groups.items():
            def rank_key(item: CleanRescoreItem):
                snapshot = json.loads(item.vacancy_snapshot or "{}")
                raw_found_at = str(snapshot.get("found_at") or "")
                try:
                    found_ts = datetime.fromisoformat(raw_found_at).timestamp()
                except ValueError:
                    found_ts = 0.0
                return (
                    0 if item.base_routing_class == "CLEAN_STRONG" else 1,
                    -(item.invite_score or -1),
                    -(item.fit_score or -1),
                    -found_ts,
                )

            items.sort(key=rank_key)
            active_ids = active_clean.get(key, set())
            for rank, item in enumerate(items, start=1):
                item.company_rank = rank
                reasons = json.loads(item.route_reason_codes or "[]")

                if active_ids and item.vacancy_id not in active_ids:
                    item.company_state = "ACTIVE_CLEAN"
                    item.routing_class = "COMPANY_RESERVE"
                    reasons.append("active_clean_company")
                elif rank == 1:
                    item.company_state = "PRIMARY"
                else:
                    item.company_state = "RESERVE"
                    item.routing_class = "COMPANY_RESERVE"
                    reasons.append("company_reserve")

                item.route_reason_codes = _json(list(dict.fromkeys(reasons)))

        session.commit()
    finally:
        session.close()


def _refresh_run(run_id: int) -> CleanRescoreRun:
    session = SessionLocal()
    try:
        run = session.get(CleanRescoreRun, int(run_id))
        if run is None:
            raise ValueError(f"clean rescore run not found: {run_id}")

        counts = dict(
            session.execute(
                select(
                    CleanRescoreItem.status,
                    func.count(CleanRescoreItem.id),
                )
                .where(CleanRescoreItem.run_id == run.id)
                .group_by(CleanRescoreItem.status)
            ).all()
        )
        pending = int(counts.get("pending", 0))
        ok = int(counts.get("ok", 0))
        errors = int(counts.get("error", 0))
        clean_candidates = session.scalar(
            select(func.count(CleanRescoreItem.id)).where(
                CleanRescoreItem.run_id == run.id,
                CleanRescoreItem.status == "ok",
                CleanRescoreItem.routing_class.in_(CLEAN_ROUTES),
            )
        ) or 0

        run.processed_count = ok + errors
        run.ok_count = ok
        run.error_count = errors
        run.clean_candidate_count = int(clean_candidates)
        if pending == 0 and errors == 0:
            run.status = "completed"
            run.completed_at = _now()
        elif pending == 0:
            run.status = "needs_retry"
            run.completed_at = None
        else:
            run.status = "running"
            run.completed_at = None

        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run
    finally:
        session.close()


def _materialize_absolute_gates(run_id: int) -> int:
    """Persist deterministic global stops without spending an LLM call."""
    session = SessionLocal()
    materialized = 0
    try:
        rows = session.scalars(
            select(CleanRescoreItem).where(
                CleanRescoreItem.run_id == int(run_id),
                CleanRescoreItem.status == "pending",
            )
        ).all()

        for item in rows:
            snapshot = json.loads(item.vacancy_snapshot or "{}")
            if not _salary_stop(
                salary_from=snapshot.get("salary_from"),
                salary_to=snapshot.get("salary_to"),
                salary_currency=snapshot.get("salary_currency"),
            ):
                continue

            item.status = "ok"
            item.availability_status = "not_required"
            item.availability_checked_at = None
            item.fit_score = None
            item.invite_score = None
            item.role_family = None
            item.role_confidence_pct = None
            item.hard_stops = _json(["salary_floor"])
            item.base_routing_class = "SKIP"
            item.routing_class = "SKIP"
            item.route_reason_codes = _json(
                ["HARD_STOP:salary_floor"]
            )
            item.company_entity_key = normalize_company_key(
                str(snapshot.get("company") or "")
            ) or None
            item.company_rank = None
            item.company_state = None
            item.extraction_json = _json(
                {
                    "fast_path": "absolute_gate",
                    "hard_stop": "salary_floor",
                }
            )
            item.error = None
            item.updated_at = _now()
            materialized += 1

        if materialized:
            session.commit()
        return materialized
    finally:
        session.close()


def reconcile_rescore_run(run_id: int) -> CleanRescoreRun:
    _rank_companies(run_id)
    return _refresh_run(run_id)


def process_rescore_batch(
    *,
    run_id: int,
    candidate_facts: str,
    recruiter_visible_resume: str,
    candidate_profile_version: str,
    recruiter_resume_version: str,
    evaluator: CleanShadowEvaluator | None = None,
    limit: int = 20,
    availability_probe: AvailabilityProbe | None = None,
    max_runtime_seconds: float | None = None,
    item_start_guard_seconds: float = 180.0,
) -> BatchResult:
    limit = max(1, int(limit))
    if max_runtime_seconds is not None:
        max_runtime_seconds = max(0.0, float(max_runtime_seconds))
    item_start_guard_seconds = max(
        0.0,
        float(item_start_guard_seconds),
    )

    reconcile_rescore_run(run_id)

    session = SessionLocal()
    try:
        run = session.get(CleanRescoreRun, int(run_id))
        if run is None:
            raise ValueError(f"clean rescore run not found: {run_id}")
        if run.status not in OPEN_RUN_STATUSES:
            raise RuntimeError(
                f"clean rescore run {run.id} is {run.status}, not resumable"
            )
        learned_patterns = _validate_run_versions(
            run,
            candidate_profile_version=candidate_profile_version,
            recruiter_resume_version=recruiter_resume_version,
        )
    finally:
        session.close()

    fast_path_processed = _materialize_absolute_gates(run_id)

    session = SessionLocal()
    try:
        pending_ids = session.scalars(
            select(CleanRescoreItem.id)
            .join(Vacancy, Vacancy.id == CleanRescoreItem.vacancy_id)
            .where(
                CleanRescoreItem.run_id == int(run_id),
                CleanRescoreItem.status == "pending",
            )
            .order_by(Vacancy.found_at.desc(), Vacancy.id.desc())
            .limit(limit)
        ).all()
    finally:
        session.close()

    scorer = evaluator or CleanShadowEvaluator(
        learned_patterns=learned_patterns,
    )
    batch_processed = fast_path_processed
    batch_failed = 0
    budget_exhausted = False
    batch_started = time.monotonic()

    for item_id in pending_ids:
        if max_runtime_seconds is not None:
            elapsed = time.monotonic() - batch_started
            remaining = max_runtime_seconds - elapsed
            if remaining <= item_start_guard_seconds:
                budget_exhausted = True
                break
        session = SessionLocal()
        try:
            item = session.get(CleanRescoreItem, int(item_id))
            if item is None or item.status != "pending":
                continue
            snapshot = json.loads(item.vacancy_snapshot or "{}")
        finally:
            session.close()

        try:
            extraction = scorer.evaluate(
                candidate_facts=candidate_facts,
                recruiter_visible_resume=recruiter_visible_resume,
                vacancy=_vacancy_text(snapshot),
                cover_letter="",
            )
            scores = build_shadow_scores(
                extraction,
                salary_from=snapshot.get("salary_from"),
                salary_to=snapshot.get("salary_to"),
                salary_currency=snapshot.get("salary_currency"),
                description=str(snapshot.get("description") or ""),
                recruiter_visible_resume=recruiter_visible_resume,
                vacancy_context=_vacancy_text(snapshot),
            )

            availability = "not_required"
            checked_at = None
            if scores.routing_class in CLEAN_ROUTES:
                if availability_probe is None:
                    availability = "not_checked"
                else:
                    availability = str(
                        availability_probe(snapshot) or "unresolved"
                    )
                    if availability not in {
                        "active",
                        "closed",
                        "unresolved",
                    }:
                        availability = "unresolved"
                    checked_at = _now()

            route, extra_reason = _availability_route(
                scores.routing_class,
                availability,
            )
            reasons = list(scores.route_reason_codes)
            if extra_reason:
                reasons.append(extra_reason)

            session = SessionLocal()
            try:
                item = session.get(CleanRescoreItem, int(item_id))
                if item is None:
                    raise RuntimeError(
                        f"clean rescore item disappeared: {item_id}"
                    )
                item.status = "ok"
                item.availability_status = availability
                item.availability_checked_at = checked_at
                item.fit_score = scores.fit_score
                item.invite_score = scores.invite_score
                item.role_family = extraction.role_family_primary
                item.role_confidence_pct = int(
                    round(100 * extraction.role_confidence)
                )
                item.hard_stops = _json(list(scores.hard_stops))
                item.base_routing_class = scores.routing_class
                item.routing_class = route
                item.route_reason_codes = _json(
                    list(dict.fromkeys(reasons))
                )
                item.company_entity_key = normalize_company_key(
                    str(snapshot.get("company") or "")
                ) or None
                item.extraction_json = extraction.model_dump_json()
                item.error = None
                item.updated_at = _now()
                session.commit()
            finally:
                session.close()

            batch_processed += 1
            _refresh_run(run_id)
        except Exception as exc:
            session = SessionLocal()
            try:
                item = session.get(CleanRescoreItem, int(item_id))
                if item is not None:
                    item.status = "error"
                    item.error = (
                        f"{type(exc).__name__}: {exc}"
                    )[:4000]
                    item.updated_at = _now()
                    session.commit()
            finally:
                session.close()
            batch_failed += 1
            _refresh_run(run_id)

    _rank_companies(run_id)
    run = _refresh_run(run_id)
    return BatchResult(
        run_id=run.id,
        selected_count=run.selected_count,
        processed_count=run.processed_count,
        ok_count=run.ok_count,
        error_count=run.error_count,
        clean_candidate_count=run.clean_candidate_count,
        status=run.status,
        batch_processed=batch_processed,
        batch_failed=batch_failed,
        budget_exhausted=budget_exhausted,
    )


def get_rescore_summary(run_id: int) -> dict:
    session = SessionLocal()
    try:
        run = session.get(CleanRescoreRun, int(run_id))
        if run is None:
            raise ValueError(f"clean rescore run not found: {run_id}")
        status_counts = dict(
            session.execute(
                select(
                    CleanRescoreItem.status,
                    func.count(CleanRescoreItem.id),
                )
                .where(CleanRescoreItem.run_id == run.id)
                .group_by(CleanRescoreItem.status)
            ).all()
        )
        live_pending = int(status_counts.get("pending", 0))
        live_ok = int(status_counts.get("ok", 0))
        live_errors = int(status_counts.get("error", 0))
        live_processed = live_ok + live_errors
        live_selected = sum(int(value) for value in status_counts.values())
        live_clean_candidates = session.scalar(
            select(func.count(CleanRescoreItem.id)).where(
                CleanRescoreItem.run_id == run.id,
                CleanRescoreItem.status == "ok",
                CleanRescoreItem.routing_class.in_(CLEAN_ROUTES),
            )
        ) or 0
        if live_pending == 0 and live_errors == 0:
            live_status = "completed"
        elif live_pending == 0:
            live_status = "needs_retry"
        else:
            live_status = "running"

        availability = dict(
            session.execute(
                select(
                    CleanRescoreItem.availability_status,
                    func.count(CleanRescoreItem.id),
                )
                .where(CleanRescoreItem.run_id == run.id)
                .group_by(CleanRescoreItem.availability_status)
            ).all()
        )
        routes = dict(
            session.execute(
                select(
                    CleanRescoreItem.routing_class,
                    func.count(CleanRescoreItem.id),
                )
                .where(
                    CleanRescoreItem.run_id == run.id,
                    CleanRescoreItem.status == "ok",
                )
                .group_by(CleanRescoreItem.routing_class)
            ).all()
        )
        return {
            "run_id": run.id,
            "status": live_status,
            "window_from": run.window_from.isoformat(),
            "window_to": run.window_to.isoformat(),
            "dataset_hash": run.dataset_hash,
            "selected_count": live_selected,
            "processed_count": live_processed,
            "ok_count": live_ok,
            "error_count": live_errors,
            "clean_candidate_count": int(live_clean_candidates),
            "versions": {
                "rescore": run.rescore_version,
                "candidate_profile": run.candidate_profile_version,
                "recruiter_resume": run.recruiter_resume_version,
                "learned_patterns": run.learned_patterns_version,
                "prompt": run.prompt_version,
                "scoring": run.scoring_version,
                "gates": run.gate_version,
                "routing": run.routing_version,
                "company": run.company_policy_version,
            },
            "availability": availability,
            "routes": {
                str(key): int(value)
                for key, value in routes.items()
            },
        }
    finally:
        session.close()
