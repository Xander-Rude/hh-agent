from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select

from app.clean_live_guard import (
    assessment_version_filters,
    current_policy_context,
)
from app.clean_shadow import (
    CleanShadowExtraction,
    _normalize_extraction,
    build_shadow_scores,
)
from app.db import CleanShadowAssessment, SessionLocal, Vacancy
from app.preferences import load_preferences
from clean_shadow import _rank_companies, _vacancy_text


ROOT = Path(__file__).resolve().parent
VISIBLE_RESUME_PATH = ROOT / "data" / "clean_resume_visible.txt"


def _latest_current_rows(session):
    context = current_policy_context()
    latest = (
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
        .join(CleanShadowAssessment, CleanShadowAssessment.id == latest)
        .order_by(Vacancy.id)
    ).all()
    return context, rows


def repair(*, apply: bool) -> int:
    visible_resume = VISIBLE_RESUME_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    preferences = load_preferences()

    session = SessionLocal()
    changed = 0
    base_route_changes = 0
    family_changes = 0
    fit_changes = 0
    invite_changes = 0
    hard_stop_changes = 0
    examples: list[str] = []

    try:
        context, rows = _latest_current_rows(session)

        for vacancy, assessment in rows:
            try:
                extraction = CleanShadowExtraction.model_validate_json(
                    assessment.extraction_json or "{}"
                )
            except Exception:
                continue

            vacancy_context = _vacancy_text(vacancy)
            normalized = _normalize_extraction(
                extraction,
                vacancy=vacancy_context,
            )
            scores = build_shadow_scores(
                normalized,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=vacancy.salary_currency,
                description=vacancy.description or "",
                recruiter_visible_resume=visible_resume,
                vacancy_context=vacancy_context,
                company=vacancy.company,
                preferences=preferences,
            )

            new_hard_stops = json.dumps(
                list(scores.hard_stops),
                ensure_ascii=False,
            )
            new_reasons = json.dumps(
                list(scores.route_reason_codes),
                ensure_ascii=False,
            )

            extraction_changed = normalized != extraction
            family_changed = (
                str(assessment.role_family or "")
                != normalized.role_family_primary
            )
            base_route_changed = (
                str(assessment.base_routing_class or "")
                != scores.routing_class
            )
            fit_changed = assessment.fit_score != scores.fit_score
            invite_changed = assessment.invite_score != scores.invite_score
            hard_stops_changed = (
                str(assessment.hard_stops or "[]")
                != new_hard_stops
            )
            reasons_changed = (
                str(assessment.route_reason_codes or "[]")
                != new_reasons
            )

            if not any(
                (
                    extraction_changed,
                    family_changed,
                    base_route_changed,
                    fit_changed,
                    invite_changed,
                    hard_stops_changed,
                    reasons_changed,
                )
            ):
                continue

            changed += 1
            base_route_changes += int(base_route_changed)
            family_changes += int(family_changed)
            fit_changes += int(fit_changed)
            invite_changes += int(invite_changed)
            hard_stop_changes += int(hard_stops_changed)

            if len(examples) < 40:
                examples.append(
                    f"vacancy={vacancy.id} "
                    f"family={assessment.role_family}->{normalized.role_family_primary} "
                    f"route={assessment.base_routing_class}->{scores.routing_class} "
                    f"fit={assessment.fit_score}->{scores.fit_score} "
                    f"invite={assessment.invite_score}->{scores.invite_score} "
                    f"stops={list(scores.hard_stops)} "
                    f"{vacancy.company or ''} | {vacancy.title or ''}"
                )

            if not apply:
                continue

            assessment.extraction_json = normalized.model_dump_json()
            assessment.role_family = normalized.role_family_primary
            assessment.role_confidence_pct = int(
                round(100 * normalized.role_confidence)
            )
            assessment.fit_score = scores.fit_score
            assessment.invite_score = scores.invite_score
            assessment.hard_stops = new_hard_stops
            assessment.base_routing_class = scores.routing_class
            assessment.routing_class = scores.routing_class
            assessment.route_reason_codes = new_reasons

        print(
            "[CLEAN PRECISION REPAIR] "
            f"mode={'apply' if apply else 'dry-run'} "
            f"changed={changed} "
            f"base_route_changes={base_route_changes} "
            f"family_changes={family_changes} "
            f"fit_changes={fit_changes} "
            f"invite_changes={invite_changes} "
            f"hard_stop_changes={hard_stop_changes}"
        )
        for line in examples:
            print(f"[CLEAN PRECISION REPAIR] {line}")

        if apply and changed:
            session.flush()
            _rank_companies(
                session,
                context.learned_patterns_version,
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
            "Persist deterministic CLEAN normalization and current hard stops "
            "without any LLM calls."
        ),
    )
    args = parser.parse_args()
    return repair(apply=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
