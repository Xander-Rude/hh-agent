from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select

from app.clean_live_guard import (
    CANDIDATE_PROFILE_VERSION,
    RECRUITER_RESUME_VERSION,
    current_learned_patterns_version,
)
from app.clean_shadow import (
    COMPANY_POLICY_VERSION,
    GATE_VERSION,
    PROMPT_VERSION,
    ROUTING_VERSION,
    SCORING_VERSION,
    CleanShadowExtraction,
    _normalize_extraction,
    build_shadow_scores,
    normalize_company_key,
)
from app.db import CleanShadowAssessment, SessionLocal, Vacancy
from app.preferences import load_preferences
from clean_shadow import _rank_companies, _vacancy_text


ROOT = Path(__file__).resolve().parent
VISIBLE_RESUME_PATH = ROOT / "data" / "clean_resume_visible.txt"
SOURCE_SCORING_VERSION = "clean-shadow-score-v3"


def _version_filters(*, scoring_version: str):
    filters = [
        CleanShadowAssessment.status == "ok",
        (
            CleanShadowAssessment.candidate_profile_version
            == CANDIDATE_PROFILE_VERSION
        ),
        (
            CleanShadowAssessment.recruiter_resume_version
            == RECRUITER_RESUME_VERSION
        ),
        CleanShadowAssessment.prompt_version == PROMPT_VERSION,
        CleanShadowAssessment.scoring_version == scoring_version,
        CleanShadowAssessment.gate_version == GATE_VERSION,
        CleanShadowAssessment.routing_version == ROUTING_VERSION,
        (
            CleanShadowAssessment.company_policy_version
            == COMPANY_POLICY_VERSION
        ),
    ]
    learned_patterns_version = current_learned_patterns_version()
    if learned_patterns_version is None:
        filters.append(
            CleanShadowAssessment.learned_patterns_version.is_(None)
        )
    else:
        filters.append(
            CleanShadowAssessment.learned_patterns_version
            == learned_patterns_version
        )
    return tuple(filters), learned_patterns_version


def _latest_rows(session, *, scoring_version: str):
    filters, learned_patterns_version = _version_filters(
        scoring_version=scoring_version,
    )
    latest = (
        select(func.max(CleanShadowAssessment.id))
        .where(
            CleanShadowAssessment.vacancy_id == Vacancy.id,
            *filters,
        )
        .correlate(Vacancy)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Vacancy, CleanShadowAssessment)
        .join(CleanShadowAssessment, CleanShadowAssessment.id == latest)
        .order_by(Vacancy.id)
    ).all()
    return rows, learned_patterns_version


def migrate(*, apply: bool) -> int:
    if SCORING_VERSION == SOURCE_SCORING_VERSION:
        raise RuntimeError(
            "target scoring version must differ from source scoring version"
        )

    visible_resume = VISIBLE_RESUME_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    preferences = load_preferences()

    session = SessionLocal()
    source_count = 0
    existing_count = 0
    create_count = 0
    fit_changes = 0
    base_route_changes = 0
    examples: list[str] = []

    try:
        rows, learned_patterns_version = _latest_rows(
            session,
            scoring_version=SOURCE_SCORING_VERSION,
        )
        source_count = len(rows)

        existing_rows, _ = _latest_rows(
            session,
            scoring_version=SCORING_VERSION,
        )
        existing_by_vacancy = {
            vacancy.id: assessment
            for vacancy, assessment in existing_rows
        }

        for vacancy, source in rows:
            if vacancy.id in existing_by_vacancy:
                existing_count += 1
                continue

            extraction = CleanShadowExtraction.model_validate_json(
                source.extraction_json or "{}"
            )
            vacancy_context = _vacancy_text(vacancy)
            extraction = _normalize_extraction(
                extraction,
                vacancy=vacancy_context,
            )
            scores = build_shadow_scores(
                extraction,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=vacancy.salary_currency,
                description=vacancy.description or "",
                recruiter_visible_resume=visible_resume,
                vacancy_context=vacancy_context,
                company=vacancy.company,
                preferences=preferences,
            )

            create_count += 1
            fit_changes += int(source.fit_score != scores.fit_score)
            base_route_changes += int(
                str(source.base_routing_class or "")
                != scores.routing_class
            )

            if len(examples) < 30:
                examples.append(
                    f"vacancy={vacancy.id} "
                    f"fit={source.fit_score}->{scores.fit_score} "
                    f"route={source.base_routing_class}->{scores.routing_class} "
                    f"invite={source.invite_score}->{scores.invite_score} "
                    f"{vacancy.company or ''} | {vacancy.title or ''}"
                )

            if not apply:
                continue

            session.add(
                CleanShadowAssessment(
                    vacancy_id=vacancy.id,
                    legacy_evaluation_id=source.legacy_evaluation_id,
                    status="ok",
                    fit_score=scores.fit_score,
                    invite_score=scores.invite_score,
                    role_family=extraction.role_family_primary,
                    role_confidence_pct=int(
                        round(100 * extraction.role_confidence)
                    ),
                    hard_stops=json.dumps(
                        list(scores.hard_stops),
                        ensure_ascii=False,
                    ),
                    base_routing_class=scores.routing_class,
                    routing_class=scores.routing_class,
                    route_reason_codes=json.dumps(
                        list(scores.route_reason_codes),
                        ensure_ascii=False,
                    ),
                    company_entity_key=normalize_company_key(
                        vacancy.company
                    ) or None,
                    company_rank=None,
                    company_state=None,
                    extraction_json=extraction.model_dump_json(),
                    candidate_profile_version=CANDIDATE_PROFILE_VERSION,
                    recruiter_resume_version=RECRUITER_RESUME_VERSION,
                    learned_patterns_version=learned_patterns_version,
                    prompt_version=PROMPT_VERSION,
                    scoring_version=SCORING_VERSION,
                    gate_version=GATE_VERSION,
                    routing_version=ROUTING_VERSION,
                    company_policy_version=COMPANY_POLICY_VERSION,
                    error=None,
                )
            )

        print(
            "[CLEAN SCORE V4] "
            f"mode={'apply' if apply else 'dry-run'} "
            f"source={source_count} existing_v4={existing_count} "
            f"create={create_count} fit_changes={fit_changes} "
            f"base_route_changes={base_route_changes}"
        )
        for line in examples:
            print(f"[CLEAN SCORE V4] {line}")

        if apply and create_count:
            session.flush()
            _rank_companies(
                session,
                learned_patterns_version,
            )
        elif apply:
            session.commit()
        else:
            session.rollback()

        return 0
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Create score-v4 CLEAN assessment snapshots from current "
            "score-v3 extractions without any LLM calls."
        ),
    )
    args = parser.parse_args()
    return migrate(apply=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
