from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    CalibrationRun,
    SessionLocal,
    StrategyBadExample,
    StrategyCandidateProfile,
    StrategyGoodExample,
    StrategyLearnedPattern,
    StrategyMemoryActivation,
    StrategyMemoryState,
    StrategyMemoryVersion,
    StrategyTargetStrategy,
)


MEMORY_SCHEMA_VERSION = "strategy-memory-v1"
ACTIVATION_STATE_ID = 1

PATTERN_TYPES = {
    "positive",
    "negative",
    "gap",
    "role",
    "domain",
    "responsibility",
    "seniority",
    "salary",
    "company",
    "evidence",
    "other",
}


def _stamp() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _normalize_pattern(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("pattern_key") or item.get("key") or "").strip()
    pattern_type = str(item.get("pattern_type") or item.get("type") or "").strip()
    statement = str(item.get("statement") or "").strip()

    if not key:
        raise ValueError("learned pattern requires pattern_key")
    if pattern_type not in PATTERN_TYPES:
        raise ValueError(f"unsupported learned pattern type: {pattern_type}")
    if not statement:
        raise ValueError("learned pattern requires statement")

    support_count = int(item.get("support_count") or 0)
    if support_count < 0:
        raise ValueError("support_count must be >= 0")

    confidence = item.get("confidence_score")
    if confidence is not None:
        confidence = int(confidence)
        if not 0 <= confidence <= 100:
            raise ValueError("confidence_score must be between 0 and 100")

    evidence_ids = [
        int(value)
        for value in (item.get("evidence_application_ids") or [])
    ]

    source_run = item.get("source_calibration_run_id")
    if source_run is not None:
        source_run = int(source_run)

    return {
        "pattern_key": key,
        "pattern_type": pattern_type,
        "statement": statement,
        "support_count": support_count,
        "confidence_score": confidence,
        "source_calibration_run_id": source_run,
        "evidence_application_ids": evidence_ids,
    }


def _normalize_example(item: dict[str, Any]) -> dict[str, Any]:
    application_id = item.get("application_id")
    snapshot_id = item.get("decision_snapshot_id")
    event_id = item.get("outcome_event_id")

    if application_id is not None:
        application_id = int(application_id)
    if snapshot_id is not None:
        snapshot_id = int(snapshot_id)
    if event_id is not None:
        event_id = int(event_id)

    if all(value is None for value in (application_id, snapshot_id, event_id)):
        raise ValueError(
            "strategy example requires application_id, "
            "decision_snapshot_id, or outcome_event_id"
        )

    return {
        "application_id": application_id,
        "decision_snapshot_id": snapshot_id,
        "outcome_event_id": event_id,
        "label": (
            str(item.get("label")).strip()
            if item.get("label") is not None
            else None
        ),
        "rationale": (
            str(item.get("rationale")).strip()
            if item.get("rationale") is not None
            else None
        ),
    }


def _validate_example_refs(session, item: dict[str, Any]) -> None:
    application_id = item["application_id"]
    snapshot_id = item["decision_snapshot_id"]
    event_id = item["outcome_event_id"]

    if application_id is not None and session.get(Application, application_id) is None:
        raise ValueError(f"application_id not found: {application_id}")

    if snapshot_id is not None:
        snapshot = session.get(ApplicationDecisionSnapshot, snapshot_id)
        if snapshot is None:
            raise ValueError(f"decision_snapshot_id not found: {snapshot_id}")
        if application_id is not None and snapshot.application_id != application_id:
            raise ValueError("decision snapshot does not belong to application")

    if event_id is not None:
        event = session.get(ApplicationEvent, event_id)
        if event is None:
            raise ValueError(f"outcome_event_id not found: {event_id}")
        if application_id is not None and event.application_id != application_id:
            raise ValueError("outcome event does not belong to application")
        if snapshot_id is not None and event.decision_snapshot_id not in {
            None,
            snapshot_id,
        }:
            raise ValueError("outcome event does not belong to decision snapshot")


def _validate_pattern_refs(session, item: dict[str, Any]) -> None:
    source_run = item["source_calibration_run_id"]
    if source_run is not None and session.get(CalibrationRun, source_run) is None:
        raise ValueError(f"calibration_run_id not found: {source_run}")

    for application_id in item["evidence_application_ids"]:
        if session.get(Application, application_id) is None:
            raise ValueError(
                f"evidence application_id not found: {application_id}"
            )


def _content_payload(
    *,
    candidate_profile: dict[str, Any],
    target_strategy: dict[str, Any],
    learned_patterns: list[dict[str, Any]],
    good_examples: list[dict[str, Any]],
    bad_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "candidate_profile": candidate_profile,
        "target_strategy": target_strategy,
        "learned_patterns": learned_patterns,
        "good_examples": good_examples,
        "bad_examples": bad_examples,
    }


def _content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _active_version_id(session) -> int | None:
    state = session.get(StrategyMemoryState, ACTIVATION_STATE_ID)
    return state.active_version_id if state is not None else None


def create_memory_version(
    *,
    candidate_profile: dict[str, Any],
    target_strategy: dict[str, Any],
    learned_patterns: list[dict[str, Any]] | None = None,
    good_examples: list[dict[str, Any]] | None = None,
    bad_examples: list[dict[str, Any]] | None = None,
    candidate_profile_source_ref: str | None = None,
    candidate_profile_source_hash: str | None = None,
    parent_version_id: int | None = None,
    source: str = "manual",
    calibration_run_id: int | None = None,
    note: str | None = None,
    activate: bool = False,
) -> StrategyMemoryVersion:
    if not isinstance(candidate_profile, dict):
        raise ValueError("candidate_profile must be an object")
    if not isinstance(target_strategy, dict):
        raise ValueError("target_strategy must be an object")

    patterns = [
        _normalize_pattern(item)
        for item in (learned_patterns or [])
    ]
    good = [
        _normalize_example(item)
        for item in (good_examples or [])
    ]
    bad = [
        _normalize_example(item)
        for item in (bad_examples or [])
    ]

    payload = _content_payload(
        candidate_profile=candidate_profile,
        target_strategy=target_strategy,
        learned_patterns=patterns,
        good_examples=good,
        bad_examples=bad,
    )
    digest = _content_hash(payload)

    session = SessionLocal()
    try:
        existing = session.scalar(
            select(StrategyMemoryVersion).where(
                StrategyMemoryVersion.content_hash == digest
            )
        )
        if existing is not None:
            version_id = existing.id
            session.commit()
        else:
            if calibration_run_id is not None:
                calibration_run_id = int(calibration_run_id)
                if session.get(CalibrationRun, calibration_run_id) is None:
                    raise ValueError(
                        f"calibration_run_id not found: {calibration_run_id}"
                    )

            if parent_version_id is None:
                parent_version_id = _active_version_id(session)
            elif session.get(StrategyMemoryVersion, parent_version_id) is None:
                raise ValueError(
                    f"parent strategy memory version not found: "
                    f"{parent_version_id}"
                )

            for item in patterns:
                _validate_pattern_refs(session, item)
            for item in good:
                _validate_example_refs(session, item)
            for item in bad:
                _validate_example_refs(session, item)

            max_version = session.scalar(
                select(func.max(StrategyMemoryVersion.version_number))
            )
            version = StrategyMemoryVersion(
                version_number=int(max_version or 0) + 1,
                parent_version_id=parent_version_id,
                source=(source or "manual").strip() or "manual",
                calibration_run_id=calibration_run_id,
                content_hash=digest,
                note=(note.strip() if note else None),
            )
            session.add(version)
            session.flush()

            session.add(
                StrategyCandidateProfile(
                    memory_version_id=version.id,
                    payload_json=_canonical_json(candidate_profile),
                    source_ref=(
                        candidate_profile_source_ref.strip()
                        if candidate_profile_source_ref
                        else None
                    ),
                    source_hash=(
                        candidate_profile_source_hash.strip()
                        if candidate_profile_source_hash
                        else None
                    ),
                )
            )
            session.add(
                StrategyTargetStrategy(
                    memory_version_id=version.id,
                    payload_json=_canonical_json(target_strategy),
                )
            )

            for item in patterns:
                session.add(
                    StrategyLearnedPattern(
                        memory_version_id=version.id,
                        pattern_key=item["pattern_key"],
                        pattern_type=item["pattern_type"],
                        statement=item["statement"],
                        support_count=item["support_count"],
                        confidence_score=item["confidence_score"],
                        source_calibration_run_id=item[
                            "source_calibration_run_id"
                        ],
                        evidence_application_ids_json=_canonical_json(
                            item["evidence_application_ids"]
                        ),
                    )
                )

            for model, items in (
                (StrategyGoodExample, good),
                (StrategyBadExample, bad),
            ):
                for item in items:
                    session.add(
                        model(
                            memory_version_id=version.id,
                            application_id=item["application_id"],
                            decision_snapshot_id=item["decision_snapshot_id"],
                            outcome_event_id=item["outcome_event_id"],
                            label=item["label"],
                            rationale=item["rationale"],
                        )
                    )

            session.commit()
            version_id = version.id

        if activate:
            session.close()
            activate_memory_version(
                version_id,
                reason="create_and_activate",
                note=note,
            )
            session = SessionLocal()

        result = session.get(StrategyMemoryVersion, version_id)
        if result is None:
            raise RuntimeError("strategy memory version disappeared")
        session.expunge(result)
        return result
    finally:
        session.close()


def activate_memory_version(
    version_id: int,
    *,
    reason: str = "activate",
    note: str | None = None,
) -> bool:
    session = SessionLocal()
    try:
        version = session.get(StrategyMemoryVersion, int(version_id))
        if version is None:
            raise ValueError(f"strategy memory version not found: {version_id}")

        state = session.get(StrategyMemoryState, ACTIVATION_STATE_ID)
        if state is None:
            state = StrategyMemoryState(
                id=ACTIVATION_STATE_ID,
                active_version_id=None,
            )
            session.add(state)
            session.flush()

        previous = state.active_version_id
        if previous == version.id:
            return False

        state.active_version_id = version.id
        state.updated_at = _stamp()
        session.add(
            StrategyMemoryActivation(
                version_id=version.id,
                previous_version_id=previous,
                reason=(reason or "activate").strip() or "activate",
                note=(note.strip() if note else None),
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def rollback_memory_version(
    version_id: int,
    *,
    note: str | None = None,
) -> bool:
    return activate_memory_version(
        version_id,
        reason="rollback",
        note=note,
    )


def _bundle(session, version: StrategyMemoryVersion) -> dict[str, Any]:
    profile = session.scalar(
        select(StrategyCandidateProfile).where(
            StrategyCandidateProfile.memory_version_id == version.id
        )
    )
    strategy = session.scalar(
        select(StrategyTargetStrategy).where(
            StrategyTargetStrategy.memory_version_id == version.id
        )
    )
    patterns = session.scalars(
        select(StrategyLearnedPattern)
        .where(StrategyLearnedPattern.memory_version_id == version.id)
        .order_by(StrategyLearnedPattern.id)
    ).all()
    good = session.scalars(
        select(StrategyGoodExample)
        .where(StrategyGoodExample.memory_version_id == version.id)
        .order_by(StrategyGoodExample.id)
    ).all()
    bad = session.scalars(
        select(StrategyBadExample)
        .where(StrategyBadExample.memory_version_id == version.id)
        .order_by(StrategyBadExample.id)
    ).all()

    def example_payload(item) -> dict[str, Any]:
        return {
            "application_id": item.application_id,
            "decision_snapshot_id": item.decision_snapshot_id,
            "outcome_event_id": item.outcome_event_id,
            "label": item.label,
            "rationale": item.rationale,
        }

    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "version": {
            "id": version.id,
            "version_number": version.version_number,
            "parent_version_id": version.parent_version_id,
            "source": version.source,
            "calibration_run_id": version.calibration_run_id,
            "content_hash": version.content_hash,
            "note": version.note,
            "created_at": (
                version.created_at.isoformat()
                if version.created_at is not None
                else None
            ),
        },
        "candidate_profile": (
            _decode_json(profile.payload_json, {})
            if profile is not None
            else {}
        ),
        "candidate_profile_source_ref": (
            profile.source_ref if profile is not None else None
        ),
        "candidate_profile_source_hash": (
            profile.source_hash if profile is not None else None
        ),
        "target_strategy": (
            _decode_json(strategy.payload_json, {})
            if strategy is not None
            else {}
        ),
        "learned_patterns": [
            {
                "pattern_key": item.pattern_key,
                "pattern_type": item.pattern_type,
                "statement": item.statement,
                "support_count": item.support_count,
                "confidence_score": item.confidence_score,
                "source_calibration_run_id": item.source_calibration_run_id,
                "evidence_application_ids": _decode_json(
                    item.evidence_application_ids_json,
                    [],
                ),
            }
            for item in patterns
        ],
        "good_examples": [example_payload(item) for item in good],
        "bad_examples": [example_payload(item) for item in bad],
    }


def get_memory_version(version_id: int) -> dict[str, Any] | None:
    session = SessionLocal()
    try:
        version = session.get(StrategyMemoryVersion, int(version_id))
        if version is None:
            return None
        return _bundle(session, version)
    finally:
        session.close()


def get_active_memory() -> dict[str, Any] | None:
    session = SessionLocal()
    try:
        active_id = _active_version_id(session)
        if active_id is None:
            return None
        version = session.get(StrategyMemoryVersion, active_id)
        if version is None:
            return None
        result = _bundle(session, version)
        result["active"] = True
        return result
    finally:
        session.close()


def list_memory_versions() -> list[dict[str, Any]]:
    session = SessionLocal()
    try:
        active_id = _active_version_id(session)
        rows = session.scalars(
            select(StrategyMemoryVersion).order_by(
                StrategyMemoryVersion.version_number.desc()
            )
        ).all()
        return [
            {
                "id": item.id,
                "version_number": item.version_number,
                "parent_version_id": item.parent_version_id,
                "source": item.source,
                "calibration_run_id": item.calibration_run_id,
                "content_hash": item.content_hash,
                "note": item.note,
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at is not None
                    else None
                ),
                "active": item.id == active_id,
            }
            for item in rows
        ]
    finally:
        session.close()
