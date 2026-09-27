from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from sqlalchemy import func, select

from app.clean_live_guard import current_policy_context
from app.clean_shadow import (
    SCORING_VERSION,
    CleanShadowExtraction,
    _normalize_extraction,
    build_shadow_scores,
)
from app.db import CleanShadowAssessment, SessionLocal, Vacancy
from app.preferences import load_preferences
from clean_shadow import _rank_companies, _vacancy_text


ROOT = Path(__file__).resolve().parent
VISIBLE_RESUME_PATH = ROOT / "data" / "clean_resume_visible.txt"
OLD_SCORING_VERSION = "clean-shadow-score-v3"


def _version_filters_without_scoring(context):
    filters = [
        CleanShadowAssessment.status == "ok",
        (
            CleanShadowAssessment.candidate_profile_version
            == context.candidate_profile_version
        ),
        (
            CleanShadowAssessment.recruiter_resume_version
            == context.recruiter_resume_version
        ),
        CleanShadowAssessment.prompt_version == context.prompt_version,
        CleanShadowAssessment.gate_version == context.gate_version,
        CleanShadowAssessment.routing_version == context.routing_version,
        (
            CleanShadowAssessment.company_policy_version
            == context.company_policy_version
        ),
        CleanShadowAssessment.scoring_version.in_(
            [OLD_SCORING_VERSION, SCORING_VERSION]
        ),
    ]
    if context.learned_patterns_version is None:
        filters.append(
            CleanShadowAssessment.learned_patterns_version.is_(None)
        )
    else:
        filters.append(
            CleanShadowAssessment.learned_patterns_version
            == context.learned_patterns_version
        )
    return tuple(filters)


def _latest_migratable_rows(session):
    context = current_policy_context()
    filters = _version_filters_without_scoring(context)
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
    return context, rows


def migrate(*, apply: bool) -> int:
    visible_resume = VISIBLE_RESUME_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    preferences = load_preferences()

    session = SessionLocal()
    changed = 0
    version_changes = 0
    fit_changes = 0
    route_changes = 0
    fit_before = collections.Counter()
    fit_after = collections.Counter()
    examples: list[str] = []

    try:
        context, rows = _latest_migratable_rows(session)

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

            fit_before[assessment.fit_score] += 1
            fit_after[scores.fit_score] += 1

            version_changed = assessment.scoring_version != SCORING_VERSION
            fit_changed = assessment.fit_score != scores.fit_score
            route_changed = (
                str(assessment.base_routing_class or "")
                != scores.routing_class
            )
            changed_row = any(
                (
                    version_changed,
                    normalized != extraction,
                    fit_changed,
                    assessment.invite_score != scores.invite_score,
                    route_changed,
                    str(assessment.hard_stops or "[]")
                    != json.dumps(list(scores.hard_stops), ensure_ascii=False),
                    str(assessment.route_reason_codes or "[]")
                    != json.dumps(
                        list(scores.route_reason_codes),
                        ensure_ascii=False,
                    ),
                )
            )
            if not changed_row:
                continue

            changed += 1
            version_changes += int(version_changed)
            fit_changes += int(fit_changed)
            route_changes += int(route_changed)

            if len(examples) < 40 and (
                fit_changed or route_changed
            ):
                examples.append(
                    f"vacancy={vacancy.id} "
                    f"fit={assessment.fit_score}->{scores.fit_score} "
                    f"route={assessment.base_routing_class}->{scores.routing_class} "
                    f"invite={assessment.invite_score}->{scores.invite_score} "
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
            assessment.hard_stops = json.dumps(
                list(scores.hard_stops),
                ensure_ascii=False,
            )
            assessment.base_routing_class = scores.routing_class
            assessment.routing_class = scores.routing_class
            assessment.route_reason_codes = json.dumps(
                list(scores.route_reason_codes),
                ensure_ascii=False,
            )
            assessment.scoring_version = SCORING_VERSION

        print(
            "[CLEAN SCORE V4] "
            f"mode={'apply' if apply else 'dry-run'} "
            f"rows={len(rows)} changed={changed} "
            f"version_changes={version_changes} "
            f"fit_changes={fit_changes} "
            f"route_changes={route_changes}"
        )
        print(
            "[CLEAN SCORE V4] fit_before="
            f"{sorted(fit_before.items(), key=lambda item: str(item[0]))}"
        )
        print(
            "[CLEAN SCORE V4] fit_after="
            f"{sorted(fit_after.items(), key=lambda item: str(item[0]))}"
        )
        for line in examples:
            print(f"[CLEAN SCORE V4] {line}")

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
        help="Persist deterministic CLEAN score-v4 recalibration.",
    )
    args = parser.parse_args()
    return migrate(apply=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
