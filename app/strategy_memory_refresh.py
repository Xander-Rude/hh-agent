from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.calibration import CalibrationLLMReport
from app.db import CalibrationRun, SessionLocal, StrategyMemoryVersion
from app.strategy_memory import (
    create_memory_version,
    get_active_memory,
    get_memory_version,
)


REFRESH_POLICY_VERSION = "calibration-memory-refresh-v1"
CONFIDENCE_SCORE = {
    "medium": 70,
    "high": 90,
}
ACTION_PATTERN_TYPE = {
    "candidate_for_evidence_rule_review": "evidence",
    "candidate_for_prompt_review": "other",
    "candidate_for_threshold_review": "other",
    "monitor": "other",
}


@dataclass(frozen=True)
class RefreshResult:
    status: str
    calibration_run_id: int
    memory_version_id: int | None
    memory_version_number: int | None
    accepted_pattern_count: int
    rejected_pattern_count: int
    activated: bool
    reason: str | None = None


def _stable_pattern_key(title: str, hypothesis: str) -> str:
    normalized = " ".join(
        re.sub(r"[^a-zа-я0-9]+", " ", f"{title} {hypothesis}".lower())
        .replace("ё", "е")
        .split()
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    prefix = "-".join(normalized.split()[:5])[:80].strip("-")
    return f"calibration:{prefix or 'pattern'}:{digest}"


def _statement(pattern) -> str:
    parts = [
        pattern.hypothesis.strip(),
        f"Observed: {pattern.observed_signal.strip()}",
        f"Caution: {pattern.caution.strip()}",
        f"Action class: {pattern.proposed_action}",
    ]
    return " ".join(part for part in parts if part)


def _existing_materialization(run_id: int) -> StrategyMemoryVersion | None:
    session = SessionLocal()
    try:
        return session.scalar(
            select(StrategyMemoryVersion)
            .where(
                StrategyMemoryVersion.calibration_run_id == run_id
            )
            .order_by(StrategyMemoryVersion.id.desc())
            .limit(1)
        )
    finally:
        session.close()


def _calibration_run(run_id: int | None) -> CalibrationRun | None:
    session = SessionLocal()
    try:
        if run_id is not None:
            return session.get(CalibrationRun, int(run_id))
        return session.scalar(
            select(CalibrationRun)
            .where(CalibrationRun.status == "completed")
            .order_by(CalibrationRun.id.desc())
            .limit(1)
        )
    finally:
        session.close()


def _guarded_patterns(report: CalibrationLLMReport) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    rejected = 0

    for pattern in report.pattern_candidates:
        if pattern.confidence not in CONFIDENCE_SCORE:
            rejected += 1
            continue

        support = sorted(set(pattern.supporting_application_ids))
        counterexamples = sorted(set(pattern.counterexample_application_ids))

        # Calibration already enforces minimum support. Refresh adds a stronger
        # noise guard: the supporting sample must strictly exceed known
        # counterexamples before the pattern can enter active strategy memory.
        if len(support) <= len(counterexamples):
            rejected += 1
            continue

        accepted.append(
            {
                "pattern_key": _stable_pattern_key(
                    pattern.title,
                    pattern.hypothesis,
                ),
                "pattern_type": ACTION_PATTERN_TYPE[
                    pattern.proposed_action
                ],
                "statement": _statement(pattern),
                "support_count": len(support),
                "confidence_score": CONFIDENCE_SCORE[
                    pattern.confidence
                ],
                "source_calibration_run_id": None,
                "evidence_application_ids": support,
            }
        )

    return accepted, rejected


def propose_memory_refresh(
    *,
    calibration_run_id: int | None = None,
) -> RefreshResult:
    run = _calibration_run(calibration_run_id)
    if run is None:
        return RefreshResult(
            status="no_completed_calibration",
            calibration_run_id=int(calibration_run_id or 0),
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason="No completed calibration run is available.",
        )

    if run.status != "completed":
        return RefreshResult(
            status="blocked",
            calibration_run_id=run.id,
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason=f"Calibration run status is {run.status}, not completed.",
        )

    existing = _existing_materialization(run.id)
    if existing is not None:
        return RefreshResult(
            status="already_materialized",
            calibration_run_id=run.id,
            memory_version_id=existing.id,
            memory_version_number=existing.version_number,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason="This calibration run already has a memory version.",
        )

    active = get_active_memory()
    if not active:
        return RefreshResult(
            status="blocked",
            calibration_run_id=run.id,
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason="No active strategy memory baseline exists.",
        )

    if not run.llm_report_json:
        return RefreshResult(
            status="blocked",
            calibration_run_id=run.id,
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason="Completed calibration run has no LLM report.",
        )

    try:
        report = CalibrationLLMReport.model_validate_json(
            run.llm_report_json
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return RefreshResult(
            status="blocked",
            calibration_run_id=run.id,
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=0,
            activated=False,
            reason=f"Invalid calibration report: {type(exc).__name__}",
        )

    new_patterns, rejected = _guarded_patterns(report)
    if not new_patterns:
        return RefreshResult(
            status="no_safe_patterns",
            calibration_run_id=run.id,
            memory_version_id=None,
            memory_version_number=None,
            accepted_pattern_count=0,
            rejected_pattern_count=rejected,
            activated=False,
            reason="No medium/high-confidence pattern passed refresh guards.",
        )

    existing_patterns = {
        item["pattern_key"]: item
        for item in (active.get("learned_patterns") or [])
    }
    for pattern in new_patterns:
        pattern["source_calibration_run_id"] = run.id
        previous = existing_patterns.get(pattern["pattern_key"])
        if previous is None:
            existing_patterns[pattern["pattern_key"]] = pattern
            continue

        previous_support = int(previous.get("support_count") or 0)
        previous_confidence = int(previous.get("confidence_score") or 0)
        if (
            pattern["support_count"] > previous_support
            or (
                pattern["support_count"] == previous_support
                and pattern["confidence_score"] >= previous_confidence
            )
        ):
            existing_patterns[pattern["pattern_key"]] = pattern

    merged_patterns = sorted(
        existing_patterns.values(),
        key=lambda item: (
            str(item.get("pattern_type") or ""),
            str(item.get("pattern_key") or ""),
        ),
    )

    version = create_memory_version(
        candidate_profile=dict(active.get("candidate_profile") or {}),
        target_strategy=dict(active.get("target_strategy") or {}),
        learned_patterns=merged_patterns,
        good_examples=list(active.get("good_examples") or []),
        bad_examples=list(active.get("bad_examples") or []),
        candidate_profile_source_ref=active.get(
            "candidate_profile_source_ref"
        ),
        candidate_profile_source_hash=active.get(
            "candidate_profile_source_hash"
        ),
        parent_version_id=int(active["version"]["id"]),
        source="calibration_batch",
        calibration_run_id=run.id,
        note=(
            f"{REFRESH_POLICY_VERSION}: proposed from calibration "
            f"run {run.id}; not auto-activated"
        ),
        activate=False,
    )

    # Re-read persisted data to ensure the version exists and remains inactive.
    persisted = get_memory_version(version.id)
    if persisted is None:
        raise RuntimeError("materialized strategy memory version disappeared")

    return RefreshResult(
        status="proposed",
        calibration_run_id=run.id,
        memory_version_id=version.id,
        memory_version_number=version.version_number,
        accepted_pattern_count=len(new_patterns),
        rejected_pattern_count=rejected,
        activated=False,
        reason=None,
    )
