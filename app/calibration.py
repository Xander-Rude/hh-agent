from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    CalibrationRun,
    CleanShadowAssessment,
    SessionLocal,
    Vacancy,
)
from app.llm import LLMProvider


PROMPT_VERSION = "calibration-report-v1"

POSITIVE_STAGE_RANK = {
    "applied": 0,
    "viewed": 10,
    "recruiter_message": 20,
    "screening_call": 30,
    "interview_scheduled": 40,
    "interview_completed": 50,
    "next_stage": 60,
    "final_stage": 70,
    "offer": 80,
}

EVENT_ALIASES = {
    "career_submitted": "applied",
    "technical_applied": "applied",
    "career_viewed": "viewed",
    "career_workflow_invited": "workflow_invited",
    "career_rejected": "rejected",
    "career_human_response": "recruiter_message",
    "career_interview_agreed": "interview_scheduled",
    "career_interview_done": "interview_completed",
}

MATURE_EVENT_TYPES = {
    "recruiter_message",
    "screening_call",
    "interview_scheduled",
    "interview_completed",
    "next_stage",
    "final_stage",
    "offer",
    "rejected",
    "no_response_30d",
}

HUMAN_EVENT_TYPES = {
    "recruiter_message",
    "screening_call",
    "interview_scheduled",
    "interview_completed",
    "next_stage",
    "final_stage",
    "offer",
    "declined_by_user",
    "withdrawn",
}

TERMINAL_EVENT_TYPES = {
    "rejected",
    "declined_by_user",
    "withdrawn",
    "vacancy_closed",
}

CAUSAL_CONFLICT_ATTRIBUTIONS = {
    "targeted_hunt",
    "assisted_multi_touch",
}

DEFAULT_MIN_MATURE = 10
DEFAULT_MIN_NEW_MATURE = 5
DEFAULT_MIN_PATTERN_SUPPORT = 3
DEFAULT_MAX_LLM_CASES = 80


@dataclass(frozen=True)
class CalibrationSettings:
    min_mature: int
    min_new_mature: int
    min_pattern_support: int
    max_llm_cases: int

    @classmethod
    def from_env(cls) -> "CalibrationSettings":
        return cls(
            min_mature=max(
                1,
                int(
                    os.getenv(
                        "CALIBRATION_MIN_MATURE",
                        str(DEFAULT_MIN_MATURE),
                    )
                ),
            ),
            min_new_mature=max(
                1,
                int(
                    os.getenv(
                        "CALIBRATION_MIN_NEW_MATURE",
                        str(DEFAULT_MIN_NEW_MATURE),
                    )
                ),
            ),
            min_pattern_support=max(
                2,
                int(
                    os.getenv(
                        "CALIBRATION_MIN_PATTERN_SUPPORT",
                        str(DEFAULT_MIN_PATTERN_SUPPORT),
                    )
                ),
            ),
            max_llm_cases=max(
                10,
                int(
                    os.getenv(
                        "CALIBRATION_MAX_LLM_CASES",
                        str(DEFAULT_MAX_LLM_CASES),
                    )
                ),
            ),
        )


class CalibrationPatternCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    hypothesis: str
    observed_signal: str
    supporting_application_ids: list[int] = Field(default_factory=list)
    counterexample_application_ids: list[int] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    proposed_action: Literal[
        "monitor",
        "candidate_for_prompt_review",
        "candidate_for_threshold_review",
        "candidate_for_evidence_rule_review",
    ]
    caution: str


class CalibrationLLMReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    pattern_candidates: list[CalibrationPatternCandidate] = Field(
        default_factory=list
    )
    data_limitations: list[str] = Field(default_factory=list)
    follow_up_checks: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class CalibrationDataset:
    cases: list[dict]
    metrics: dict
    dataset_hash: str
    event_high_watermark: int
    snapshot_high_watermark: int
    eligible_mature_ids: set[int]
    new_eligible_mature_ids: set[int]


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except Exception:
        return fallback
    return parsed


def _canonical_event_type(event_type: str) -> str:
    return EVENT_ALIASES.get(event_type, event_type)


def _latest_completed_run(session: Session) -> CalibrationRun | None:
    return session.scalar(
        select(CalibrationRun)
        .where(
            CalibrationRun.status == "completed",
            CalibrationRun.scope == "hh_clean",
        )
        .order_by(CalibrationRun.id.desc())
        .limit(1)
    )


def _sent_targeted_outreach(
    session: Session,
    vacancy_id: int,
) -> bool:
    # Lazy import keeps the outcome/calibration core independent from the
    # Targeted Hunt module during simple tooling/tests.
    try:
        from app.targeted_hunt.models import (
            OutreachAttempt,
            TargetedHuntCase,
        )
    except Exception:
        return False

    try:
        case_id = session.scalar(
            select(TargetedHuntCase.id)
            .where(TargetedHuntCase.vacancy_id == vacancy_id)
            .limit(1)
        )
        if case_id is None:
            return False

        attempt_id = session.scalar(
            select(OutreachAttempt.id)
            .where(
                OutreachAttempt.case_id == case_id,
                OutreachAttempt.sent_at.is_not(None),
            )
            .limit(1)
        )
        return attempt_id is not None
    except Exception:
        # Calibration must remain usable on databases created before
        # Targeted Hunt tables existed. Missing optional schema means
        # "no observed outreach", not a fatal calibration error.
        return False


def _shadow_features(
    session: Session,
    snapshot: ApplicationDecisionSnapshot,
) -> dict:
    if snapshot.shadow_assessment_id is None:
        return {}

    shadow = session.get(
        CleanShadowAssessment,
        snapshot.shadow_assessment_id,
    )
    if shadow is None:
        return {}

    extraction = _loads(shadow.extraction_json, {})
    if not isinstance(extraction, dict):
        extraction = {}

    return {
        "primary_object": extraction.get("primary_object"),
        "project_lifecycle_ownership": extraction.get(
            "project_lifecycle_ownership"
        ),
        "role_narrative_coherence": extraction.get(
            "role_narrative_coherence"
        ),
        "recent_relevant_evidence": extraction.get(
            "recent_relevant_evidence"
        ),
        "seniority_autonomy_visibility": extraction.get(
            "seniority_autonomy_visibility"
        ),
        "domain_technical_visibility": extraction.get(
            "domain_technical_visibility"
        ),
        "visible_differentiators": extraction.get(
            "visible_differentiators"
        ),
        "top_invite_reasons": extraction.get(
            "top_invite_reasons",
            [],
        )[:5],
        "invite_risks": extraction.get(
            "invite_risks",
            [],
        )[:5],
    }


def _case_from_snapshot(
    session: Session,
    snapshot: ApplicationDecisionSnapshot,
    previous_event_high_watermark: int,
) -> dict:
    application = session.get(Application, snapshot.application_id)
    if application is None:
        raise ValueError(
            f"snapshot {snapshot.id} has no application "
            f"{snapshot.application_id}"
        )

    event_rows = list(
        session.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(
                ApplicationEvent.observed_at.asc(),
                ApplicationEvent.id.asc(),
            )
        )
    )

    normalized_events: list[dict] = []
    flags: set[str] = set()
    max_event_id = 0
    max_mature_event_id = 0
    human_attributions: set[str] = set()
    all_attributions: set[str] = set()

    for event in event_rows:
        event_type = _canonical_event_type(event.event_type)
        flags.add(event_type)
        max_event_id = max(max_event_id, event.id)
        all_attributions.add(event.attribution or "unknown")

        if event_type in MATURE_EVENT_TYPES:
            max_mature_event_id = max(max_mature_event_id, event.id)
        if event_type in HUMAN_EVENT_TYPES:
            human_attributions.add(event.attribution or "unknown")

        normalized_events.append(
            {
                "id": event.id,
                "type": event_type,
                "attribution": event.attribution or "unknown",
                "confidence": event.confidence or "unknown",
                "observed_at": (
                    event.observed_at.isoformat()
                    if event.observed_at is not None
                    else None
                ),
            }
        )

    positive_stages = [
        name
        for name in POSITIVE_STAGE_RANK
        if name in flags
    ]
    if application.applied_at is not None and "already_applied" not in flags:
        if "applied" not in positive_stages:
            positive_stages.append("applied")

    best_positive_stage = (
        max(
            positive_stages,
            key=lambda name: POSITIVE_STAGE_RANK[name],
        )
        if positive_stages
        else None
    )

    terminal_candidates = [
        event
        for event in normalized_events
        if event["type"] in TERMINAL_EVENT_TYPES
    ]
    terminal_outcome = (
        terminal_candidates[-1]["type"]
        if terminal_candidates
        else None
    )

    already_applied = "already_applied" in flags
    transport_applied = (
        application.applied_at is not None
        and not already_applied
    )
    targeted_outreach = _sent_targeted_outreach(
        session,
        snapshot.vacancy_id,
    )

    attribution_uncertain = bool(
        human_attributions
        and (
            "unknown" in human_attributions
            or bool(
                human_attributions
                & CAUSAL_CONFLICT_ATTRIBUTIONS
            )
        )
    )

    cohort = (
        "clean_backfill"
        if snapshot.application_type == "clean_backfill"
        else (
            "assisted_multi_touch"
            if targeted_outreach and transport_applied
            else (
                "targeted_hunt"
                if targeted_outreach
                else (
                    "hh_clean"
                    if snapshot.account_key == "clean"
                    else "hh_old"
                )
            )
        )
    )

    mature = bool(flags & MATURE_EVENT_TYPES)
    eligible_clean_learning = bool(
        snapshot.account_key == "clean"
        and snapshot.application_type == "fresh_clean"
        and transport_applied
        and cohort == "hh_clean"
        and not attribution_uncertain
        and mature
    )

    vacancy_data = _loads(snapshot.vacancy_snapshot, {})
    if not isinstance(vacancy_data, dict):
        vacancy_data = {}

    case = {
        "application_id": application.id,
        "snapshot_id": snapshot.id,
        "vacancy_id": snapshot.vacancy_id,
        "title": vacancy_data.get("title"),
        "company": vacancy_data.get("company"),
        "account_key": snapshot.account_key,
        "application_type": snapshot.application_type,
        "cohort": cohort,
        "routing_class": snapshot.routing_class,
        "fit_score": snapshot.fit_score,
        "invite_score": snapshot.invite_score,
        "hard_stops": _loads(snapshot.hard_stops, []),
        "role_family": snapshot.role_family,
        "role_confidence_pct": snapshot.role_confidence_pct,
        "company_rank": snapshot.company_rank,
        "company_state": snapshot.company_state,
        "candidate_profile_version": snapshot.candidate_profile_version,
        "recruiter_resume_version": snapshot.recruiter_resume_version,
        "prompt_version": snapshot.prompt_version,
        "scoring_version": snapshot.scoring_version,
        "gate_version": snapshot.gate_version,
        "routing_version": snapshot.routing_version,
        "company_policy_version": snapshot.company_policy_version,
        "applied_at": (
            application.applied_at.isoformat()
            if application.applied_at is not None
            else None
        ),
        "transport_applied": transport_applied,
        "already_applied": already_applied,
        "best_positive_stage": best_positive_stage,
        "terminal_outcome": terminal_outcome,
        "outcome_flags": sorted(flags),
        "human_attributions": sorted(human_attributions),
        "all_attributions": sorted(all_attributions),
        "targeted_outreach": targeted_outreach,
        "attribution_uncertain": attribution_uncertain,
        "mature": mature,
        "eligible_clean_learning": eligible_clean_learning,
        "max_event_id": max_event_id,
        "max_mature_event_id": max_mature_event_id,
        "has_new_mature_outcome": (
            eligible_clean_learning
            and max_mature_event_id > previous_event_high_watermark
        ),
        **_shadow_features(session, snapshot),
    }
    return case


def _funnel_metrics(cases: list[dict]) -> dict:
    pure_clean = [
        case
        for case in cases
        if (
            case["account_key"] == "clean"
            and case["application_type"] == "fresh_clean"
            and case["transport_applied"]
            and case["cohort"] == "hh_clean"
        )
    ]

    def count_flag(flag: str) -> int:
        return sum(
            1
            for case in pure_clean
            if flag in case["outcome_flags"]
        )

    interview_completed = count_flag("interview_completed")
    applied = len(pure_clean)

    return {
        "hh_clean_pure_applied": applied,
        "hh_clean_viewed": count_flag("viewed"),
        "hh_clean_recruiter_message": count_flag("recruiter_message"),
        "hh_clean_interview_scheduled": count_flag("interview_scheduled"),
        "hh_clean_interview_completed": interview_completed,
        "hh_clean_next_stage": count_flag("next_stage"),
        "hh_clean_final_stage": count_flag("final_stage"),
        "hh_clean_offer": count_flag("offer"),
        "hh_clean_rejected": count_flag("rejected"),
        "hh_clean_no_response_7d": count_flag("no_response_7d"),
        "hh_clean_no_response_30d": count_flag("no_response_30d"),
        "interviews_per_clean_application": (
            interview_completed / applied
            if applied
            else None
        ),
    }


def build_calibration_dataset(
    session: Session,
) -> CalibrationDataset:
    previous = _latest_completed_run(session)
    previous_event_high_watermark = (
        previous.event_high_watermark
        if previous is not None
        else 0
    )

    snapshots = list(
        session.scalars(
            select(ApplicationDecisionSnapshot)
            .order_by(ApplicationDecisionSnapshot.id.asc())
        )
    )

    cases = [
        _case_from_snapshot(
            session,
            snapshot,
            previous_event_high_watermark,
        )
        for snapshot in snapshots
    ]

    event_high_watermark = int(
        session.scalar(
            select(func.max(ApplicationEvent.id))
        )
        or 0
    )
    snapshot_high_watermark = int(
        session.scalar(
            select(func.max(ApplicationDecisionSnapshot.id))
        )
        or 0
    )

    eligible_mature_ids = {
        case["application_id"]
        for case in cases
        if case["eligible_clean_learning"]
    }
    new_eligible_mature_ids = {
        case["application_id"]
        for case in cases
        if case["has_new_mature_outcome"]
    }

    cohort_counts = Counter(
        case["cohort"]
        for case in cases
    )
    role_counts = Counter(
        case["role_family"] or "unknown"
        for case in cases
        if case["eligible_clean_learning"]
    )
    best_stage_counts = Counter(
        case["best_positive_stage"] or "none"
        for case in cases
        if case["eligible_clean_learning"]
    )
    terminal_counts = Counter(
        case["terminal_outcome"] or "none"
        for case in cases
        if case["eligible_clean_learning"]
    )

    metrics = {
        "total_snapshot_cases": len(cases),
        "transport_applied_cases": sum(
            int(case["transport_applied"])
            for case in cases
        ),
        "mature_cases": sum(
            int(case["mature"])
            for case in cases
        ),
        "eligible_clean_mature_cases": len(eligible_mature_ids),
        "new_eligible_clean_mature_cases": len(
            new_eligible_mature_ids
        ),
        "cohort_counts": dict(sorted(cohort_counts.items())),
        "eligible_clean_role_counts": dict(
            sorted(role_counts.items())
        ),
        "eligible_clean_best_stage_counts": dict(
            sorted(best_stage_counts.items())
        ),
        "eligible_clean_terminal_counts": dict(
            sorted(terminal_counts.items())
        ),
        **_funnel_metrics(cases),
    }

    stable_payload = json.dumps(
        {
            "cases": cases,
            "metrics": metrics,
            "event_high_watermark": event_high_watermark,
            "snapshot_high_watermark": snapshot_high_watermark,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    dataset_hash = hashlib.sha256(
        stable_payload.encode("utf-8")
    ).hexdigest()

    return CalibrationDataset(
        cases=cases,
        metrics=metrics,
        dataset_hash=dataset_hash,
        event_high_watermark=event_high_watermark,
        snapshot_high_watermark=snapshot_high_watermark,
        eligible_mature_ids=eligible_mature_ids,
        new_eligible_mature_ids=new_eligible_mature_ids,
    )


def _response_text(response) -> str:
    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if content is not None:
        return str(content)
    if isinstance(response, dict):
        message = response.get("message") or {}
        return str(message.get("content") or "")
    return str(response)


def _case_for_llm(case: dict) -> dict:
    fields = (
        "application_id",
        "title",
        "company",
        "fit_score",
        "invite_score",
        "role_family",
        "routing_class",
        "best_positive_stage",
        "terminal_outcome",
        "outcome_flags",
        "primary_object",
        "project_lifecycle_ownership",
        "role_narrative_coherence",
        "recent_relevant_evidence",
        "seniority_autonomy_visibility",
        "domain_technical_visibility",
        "visible_differentiators",
        "top_invite_reasons",
        "invite_risks",
    )
    return {
        key: case.get(key)
        for key in fields
    }


def _validated_report(
    report: CalibrationLLMReport,
    *,
    allowed_application_ids: set[int],
    min_pattern_support: int,
) -> CalibrationLLMReport:
    accepted: list[CalibrationPatternCandidate] = []

    for pattern in report.pattern_candidates:
        support = sorted(
            set(pattern.supporting_application_ids)
            & allowed_application_ids
        )
        counterexamples = sorted(
            (
                set(pattern.counterexample_application_ids)
                & allowed_application_ids
            )
            - set(support)
        )

        if len(support) < min_pattern_support:
            continue

        confidence = pattern.confidence
        if confidence == "high" and len(support) < 5:
            confidence = "medium"

        accepted.append(
            pattern.model_copy(
                update={
                    "supporting_application_ids": support,
                    "counterexample_application_ids": counterexamples,
                    "confidence": confidence,
                }
            )
        )

    return report.model_copy(
        update={"pattern_candidates": accepted}
    )


def _llm_report(
    dataset: CalibrationDataset,
    *,
    llm: LLMProvider,
    settings: CalibrationSettings,
) -> CalibrationLLMReport:
    eligible_cases = [
        case
        for case in dataset.cases
        if case["eligible_clean_learning"]
    ]
    eligible_cases.sort(
        key=lambda item: (
            item["max_mature_event_id"],
            item["application_id"],
        ),
        reverse=True,
    )
    eligible_cases = eligible_cases[: settings.max_llm_cases]

    schema = CalibrationLLMReport.model_json_schema()
    prompt = f"""
Ты audit/calibration analyst для job-search agent.

Цель production: максимизировать реально состоявшиеся интервью на один
чистый CLEAN-отклик. Ниже только grounded cases, уже отфильтрованные Python:
fresh CLEAN, реальный новый submit, без Targeted Hunt/multi-touch и без
неопределённой human attribution.

ВАЖНО:
- FIT и INVITE уже посчитаны исторической версией алгоритма; не считай их
  истиной и не выдавай новый numeric score;
- workflow_invited НЕ равно recruiter_message и НЕ равно interview;
- rejected не стирает более ранний interview_completed/next_stage;
- no_response_30d — censored/negative-ish сигнал, не explicit reject;
- ищи только повторяющиеся наблюдаемые паттерны;
- каждая гипотеза обязана ссылаться на application_id из CASES;
- минимум {settings.min_pattern_support} supporting cases на pattern;
- обязательно ищи counterexamples;
- не меняй пороги, веса, hard stops или prompt автоматически;
- proposed_action — только кандидат на дальнейшую ручную проверку;
- не делай causal claim из корреляции и малой выборки;
- если данных мало/однородны, лучше вернуть меньше patterns.

AGGREGATE METRICS:
{json.dumps(dataset.metrics, ensure_ascii=False, indent=2)}

CASES:
{json.dumps([_case_for_llm(case) for case in eligible_cases], ensure_ascii=False, indent=2)}
""".strip()

    response = llm.chat(
        messages=[{"role": "user", "content": prompt}],
        format_schema=schema,
    )
    raw = _response_text(response).strip()
    payload = json.loads(raw)
    report = CalibrationLLMReport.model_validate(payload)
    return _validated_report(
        report,
        allowed_application_ids={
            case["application_id"]
            for case in eligible_cases
        },
        min_pattern_support=settings.min_pattern_support,
    )


def _persist_run(
    session: Session,
    *,
    dataset: CalibrationDataset,
    status: str,
    prompt_version: str,
    llm_model: str | None,
    report: CalibrationLLMReport | None = None,
    error: str | None = None,
) -> CalibrationRun:
    run = CalibrationRun(
        status=status,
        scope="hh_clean",
        event_high_watermark=dataset.event_high_watermark,
        snapshot_high_watermark=dataset.snapshot_high_watermark,
        sample_count=len(dataset.cases),
        mature_sample_count=len(dataset.eligible_mature_ids),
        new_mature_sample_count=len(
            dataset.new_eligible_mature_ids
        ),
        dataset_hash=dataset.dataset_hash,
        metrics_json=json.dumps(
            dataset.metrics,
            ensure_ascii=False,
            sort_keys=True,
        ),
        case_summaries_json=json.dumps(
            dataset.cases,
            ensure_ascii=False,
            sort_keys=True,
        ),
        llm_report_json=(
            report.model_dump_json()
            if report is not None
            else None
        ),
        prompt_version=prompt_version,
        llm_model=llm_model,
        error=error,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def run_calibration(
    *,
    llm: LLMProvider | None = None,
    settings: CalibrationSettings | None = None,
) -> CalibrationRun:
    settings = settings or CalibrationSettings.from_env()

    session = SessionLocal()
    try:
        dataset = build_calibration_dataset(session)

        if (
            len(dataset.eligible_mature_ids)
            < settings.min_mature
        ):
            return _persist_run(
                session,
                dataset=dataset,
                status="insufficient_data",
                prompt_version=PROMPT_VERSION,
                llm_model=None,
                error=(
                    "eligible mature CLEAN outcomes "
                    f"{len(dataset.eligible_mature_ids)} "
                    f"< required {settings.min_mature}"
                ),
            )

        previous = _latest_completed_run(session)
        if (
            previous is not None
            and len(dataset.new_eligible_mature_ids)
            < settings.min_new_mature
        ):
            return _persist_run(
                session,
                dataset=dataset,
                status="insufficient_data",
                prompt_version=PROMPT_VERSION,
                llm_model=None,
                error=(
                    "new mature CLEAN outcomes "
                    f"{len(dataset.new_eligible_mature_ids)} "
                    f"< required {settings.min_new_mature}"
                ),
            )

        llm = llm or LLMProvider()
        try:
            report = _llm_report(
                dataset,
                llm=llm,
                settings=settings,
            )
        except Exception as exc:
            return _persist_run(
                session,
                dataset=dataset,
                status="error",
                prompt_version=PROMPT_VERSION,
                llm_model=getattr(llm, "model", None),
                error=f"{type(exc).__name__}: {exc}",
            )

        return _persist_run(
            session,
            dataset=dataset,
            status="completed",
            prompt_version=PROMPT_VERSION,
            llm_model=getattr(llm, "model", None),
            report=report,
        )
    finally:
        session.close()
