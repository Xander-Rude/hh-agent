from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.clean_shadow import (
    COMPANY_POLICY_VERSION,
    GATE_VERSION,
    PROMPT_VERSION,
    ROUTING_VERSION,
    SCORING_VERSION,
    CleanShadowEvaluator,
    build_shadow_scores,
    normalize_company_key,
)
from app.clean_live_guard import (
    CANDIDATE_PROFILE_VERSION,
    RECRUITER_RESUME_VERSION,
)
from app.strategy_memory import get_active_memory
from app.db import (
    Application,
    CleanShadowAssessment,
    Evaluation,
    SessionLocal,
    Vacancy,
)


ROOT = Path(__file__).resolve().parent
CANDIDATE_FACTS_PATH = Path(
    os.getenv(
        "CLEAN_CANDIDATE_FACTS_PATH",
        str(ROOT / "data" / "resume.txt"),
    )
)
VISIBLE_RESUME_PATH = Path(
    os.getenv(
        "CLEAN_VISIBLE_RESUME_PATH",
        str(ROOT / "data" / "clean_resume_visible.txt"),
    )
)
MAX_PER_RUN = max(
    1,
    int(os.getenv("CLEAN_SHADOW_MAX_PER_RUN", "20")),
)
LOOKBACK_DAYS = max(
    1,
    int(os.getenv("CLEAN_SHADOW_LOOKBACK_DAYS", "7")),
)


def _vacancy_text(vacancy: Vacancy) -> str:
    salary = []
    if vacancy.salary_from is not None:
        salary.append(f"from {vacancy.salary_from}")
    if vacancy.salary_to is not None:
        salary.append(f"to {vacancy.salary_to}")
    if vacancy.salary_currency:
        salary.append(vacancy.salary_currency)

    return "\n".join(
        [
            f"Title: {vacancy.title or ''}",
            f"Company: {vacancy.company or ''}",
            f"Salary: {' '.join(salary) if salary else 'not specified'}",
            f"URL: {vacancy.url or ''}",
            "",
            vacancy.description or "",
        ]
    ).strip()


def _latest_evaluations(session):
    cutoff = (
        datetime.now(UTC).replace(tzinfo=None)
        - timedelta(days=LOOKBACK_DAYS)
    )
    latest_ids = (
        select(func.max(Evaluation.id))
        .join(Vacancy, Vacancy.id == Evaluation.vacancy_id)
        .where(
            Vacancy.source == "hh",
            Vacancy.found_at >= cutoff,
        )
        .group_by(Evaluation.vacancy_id)
    )
    return session.execute(
        select(Vacancy, Evaluation)
        .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
        .where(
            Vacancy.source == "hh",
            Vacancy.found_at >= cutoff,
            Evaluation.id.in_(latest_ids),
        )
        .order_by(Vacancy.found_at.desc(), Vacancy.id.desc())
    ).all()


def _active_strategy_memory() -> tuple[list[dict], str | None]:
    memory = get_active_memory()
    if not memory:
        return [], None

    version = memory.get("version") or {}
    version_number = version.get("version_number")
    content_hash = str(version.get("content_hash") or "").strip()
    if version_number is None or not content_hash:
        raise RuntimeError("active strategy memory has incomplete version metadata")

    token = (
        f"strategy-memory-v1:{int(version_number)}:"
        f"{content_hash[:16]}"
    )
    patterns = list(memory.get("learned_patterns") or [])
    return patterns, token


def _existing_current(
    session,
    legacy_evaluation_id: int,
    learned_patterns_version: str | None,
) -> CleanShadowAssessment | None:
    query = (
        select(CleanShadowAssessment)
        .where(
            CleanShadowAssessment.legacy_evaluation_id
            == legacy_evaluation_id,
            CleanShadowAssessment.candidate_profile_version
            == CANDIDATE_PROFILE_VERSION,
            CleanShadowAssessment.recruiter_resume_version
            == RECRUITER_RESUME_VERSION,
            CleanShadowAssessment.scoring_version
            == SCORING_VERSION,
            CleanShadowAssessment.prompt_version
            == PROMPT_VERSION,
            CleanShadowAssessment.gate_version
            == GATE_VERSION,
            CleanShadowAssessment.routing_version
            == ROUTING_VERSION,
            CleanShadowAssessment.company_policy_version
            == COMPANY_POLICY_VERSION,
        )
    )
    if learned_patterns_version is None:
        query = query.where(
            CleanShadowAssessment.learned_patterns_version.is_(None)
        )
    else:
        query = query.where(
            CleanShadowAssessment.learned_patterns_version
            == learned_patterns_version
        )

    return session.scalar(
        query
        .order_by(CleanShadowAssessment.id.desc())
        .limit(1)
    )


def _write_error(
    session,
    *,
    vacancy: Vacancy,
    evaluation: Evaluation,
    learned_patterns_version: str | None,
    error: Exception,
) -> None:
    row = _existing_current(
        session,
        evaluation.id,
        learned_patterns_version,
    )
    if row is None:
        row = CleanShadowAssessment(
            vacancy_id=vacancy.id,
            legacy_evaluation_id=evaluation.id,
            status="error",
            hard_stops="[]",
            route_reason_codes="[]",
            extraction_json="{}",
            candidate_profile_version=CANDIDATE_PROFILE_VERSION,
            recruiter_resume_version=RECRUITER_RESUME_VERSION,
            learned_patterns_version=learned_patterns_version,
            prompt_version=PROMPT_VERSION,
            scoring_version=SCORING_VERSION,
            gate_version=GATE_VERSION,
            routing_version=ROUTING_VERSION,
            company_policy_version=COMPANY_POLICY_VERSION,
        )
        session.add(row)

    row.status = "error"
    row.error = f"{type(error).__name__}: {error}"[:4000]
    session.commit()


def _active_clean_by_company(session) -> dict[str, set[int]]:
    active_statuses = {
        "approved",
        "applying",
        "applied",
        "manual_required",
        "already_applied",
    }
    result: dict[str, set[int]] = {}

    rows = session.execute(
        select(Application, Vacancy)
        .join(Vacancy, Vacancy.id == Application.vacancy_id)
        .where(
            Application.account_key == "clean",
            Application.status.in_(active_statuses),
            Vacancy.source == "hh",
        )
    ).all()

    for application, vacancy in rows:
        key = normalize_company_key(vacancy.company)
        if not key:
            continue
        result.setdefault(key, set()).add(vacancy.id)

    return result


def _rank_companies(
    session,
    learned_patterns_version: str | None,
) -> None:
    latest_query = (
        select(func.max(CleanShadowAssessment.id))
        .where(
            CleanShadowAssessment.status == "ok",
            CleanShadowAssessment.candidate_profile_version
            == CANDIDATE_PROFILE_VERSION,
            CleanShadowAssessment.recruiter_resume_version
            == RECRUITER_RESUME_VERSION,
            CleanShadowAssessment.scoring_version == SCORING_VERSION,
            CleanShadowAssessment.prompt_version == PROMPT_VERSION,
            CleanShadowAssessment.gate_version == GATE_VERSION,
            CleanShadowAssessment.routing_version == ROUTING_VERSION,
            CleanShadowAssessment.company_policy_version
            == COMPANY_POLICY_VERSION,
        )
    )
    if learned_patterns_version is None:
        latest_query = latest_query.where(
            CleanShadowAssessment.learned_patterns_version.is_(None)
        )
    else:
        latest_query = latest_query.where(
            CleanShadowAssessment.learned_patterns_version
            == learned_patterns_version
        )

    latest_shadow_ids = latest_query.group_by(
        CleanShadowAssessment.vacancy_id
    )

    rows = session.execute(
        select(CleanShadowAssessment, Vacancy)
        .join(Vacancy, Vacancy.id == CleanShadowAssessment.vacancy_id)
        .where(CleanShadowAssessment.id.in_(latest_shadow_ids))
    ).all()

    active_clean = _active_clean_by_company(session)
    groups: dict[str, list[tuple[CleanShadowAssessment, Vacancy]]] = {}

    for assessment, vacancy in rows:
        key = normalize_company_key(vacancy.company)
        assessment.company_entity_key = key or None
        assessment.company_rank = None
        assessment.company_state = None
        assessment.routing_class = assessment.base_routing_class

        if (
            key
            and assessment.base_routing_class
            in {"CLEAN_STRONG", "CLEAN_REVIEW"}
        ):
            groups.setdefault(key, []).append((assessment, vacancy))

    for key, items in groups.items():
        items.sort(
            key=lambda item: (
                0 if item[0].base_routing_class == "CLEAN_STRONG" else 1,
                -(item[0].invite_score or -1),
                -(item[0].fit_score or -1),
                -(item[1].found_at.timestamp() if item[1].found_at else 0.0),
            )
        )

        active_ids = active_clean.get(key, set())
        for rank, (assessment, vacancy) in enumerate(items, start=1):
            assessment.company_rank = rank

            if active_ids and vacancy.id not in active_ids:
                assessment.company_state = "ACTIVE_CLEAN"
                assessment.routing_class = "COMPANY_RESERVE"
                continue

            if rank == 1:
                assessment.company_state = "PRIMARY"
                continue

            assessment.company_state = "RESERVE"
            assessment.routing_class = "COMPANY_RESERVE"

    session.commit()


def main() -> int:
    if os.getenv("CLEAN_SHADOW_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        print("[CLEAN SHADOW] disabled")
        return 0

    if not CANDIDATE_FACTS_PATH.exists():
        print(f"[CLEAN SHADOW] missing candidate facts: {CANDIDATE_FACTS_PATH}")
        return 2

    if not VISIBLE_RESUME_PATH.exists():
        print(f"[CLEAN SHADOW] missing visible resume: {VISIBLE_RESUME_PATH}")
        return 2

    candidate_facts = CANDIDATE_FACTS_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    visible_resume = VISIBLE_RESUME_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )

    learned_patterns, learned_patterns_version = (
        _active_strategy_memory()
    )
    print(
        "[CLEAN SHADOW] strategy_memory="
        f"{learned_patterns_version or 'none'} "
        f"patterns={len(learned_patterns)}"
    )
    evaluator = CleanShadowEvaluator(
        learned_patterns=learned_patterns,
    )
    session = SessionLocal()
    processed = 0
    skipped = 0
    failed = 0

    try:
        for vacancy, legacy in _latest_evaluations(session):
            existing = _existing_current(
                session,
                legacy.id,
                learned_patterns_version,
            )
            if existing is not None and existing.status == "ok":
                skipped += 1
                continue
            if processed >= MAX_PER_RUN:
                break

            try:
                extraction = evaluator.evaluate(
                    candidate_facts=candidate_facts,
                    recruiter_visible_resume=visible_resume,
                    vacancy=_vacancy_text(vacancy),
                    cover_letter=legacy.cover_letter or "",
                )
                scores = build_shadow_scores(
                    extraction,
                    salary_from=vacancy.salary_from,
                    salary_to=vacancy.salary_to,
                    salary_currency=vacancy.salary_currency,
                    description=vacancy.description or "",
                    recruiter_visible_resume=visible_resume,
                    vacancy_context=_vacancy_text(vacancy),
                )

                row = existing
                if row is None:
                    row = CleanShadowAssessment(
                        vacancy_id=vacancy.id,
                        legacy_evaluation_id=legacy.id,
                        candidate_profile_version=CANDIDATE_PROFILE_VERSION,
                        recruiter_resume_version=RECRUITER_RESUME_VERSION,
                        learned_patterns_version=learned_patterns_version,
                        prompt_version=PROMPT_VERSION,
                        scoring_version=SCORING_VERSION,
                        gate_version=GATE_VERSION,
                        routing_version=ROUTING_VERSION,
                        company_policy_version=COMPANY_POLICY_VERSION,
                    )
                    session.add(row)

                row.status = "ok"
                row.learned_patterns_version = learned_patterns_version
                row.fit_score = scores.fit_score
                row.invite_score = scores.invite_score
                row.role_family = extraction.role_family_primary
                row.role_confidence_pct = int(
                    round(100 * extraction.role_confidence)
                )
                row.hard_stops = json.dumps(
                    list(scores.hard_stops),
                    ensure_ascii=False,
                )
                row.base_routing_class = scores.routing_class
                row.routing_class = scores.routing_class
                row.route_reason_codes = json.dumps(
                    list(scores.route_reason_codes),
                    ensure_ascii=False,
                )
                row.company_entity_key = normalize_company_key(vacancy.company)
                row.extraction_json = extraction.model_dump_json()
                row.error = None
                session.commit()

                processed += 1
                print(
                    "[CLEAN SHADOW] "
                    f"vacancy={vacancy.id} "
                    f"FIT={scores.fit_score} "
                    f"INVITE={scores.invite_score} "
                    f"route={scores.routing_class} "
                    f"role={extraction.role_family_primary}"
                )

            except Exception as exc:
                session.rollback()
                failed += 1
                print(
                    "[CLEAN SHADOW] ERROR "
                    f"vacancy={vacancy.id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                _write_error(
                    session,
                    vacancy=vacancy,
                    evaluation=legacy,
                    learned_patterns_version=learned_patterns_version,
                    error=exc,
                )

        _rank_companies(
            session,
            learned_patterns_version,
        )

    finally:
        session.close()

    print(
        "[CLEAN SHADOW] DONE "
        f"processed={processed} skipped={skipped} failed={failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
