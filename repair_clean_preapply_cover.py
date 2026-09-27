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
    build_shadow_scores,
    sanitize_preapply_cover_evidence,
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
    route_changes = 0
    invite_changes = 0
    fit_changes = 0
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

            sanitized = sanitize_preapply_cover_evidence(extraction)
            if sanitized == extraction:
                continue

            scores = build_shadow_scores(
                sanitized,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=vacancy.salary_currency,
                description=vacancy.description or "",
                recruiter_visible_resume=visible_resume,
                vacancy_context=_vacancy_text(vacancy),
                company=vacancy.company,
                preferences=preferences,
            )

            changed += 1
            old_route = str(assessment.base_routing_class or "")
            old_fit = assessment.fit_score
            old_invite = assessment.invite_score

            if old_route != scores.routing_class:
                route_changes += 1
            if old_fit != scores.fit_score:
                fit_changes += 1
            if old_invite != scores.invite_score:
                invite_changes += 1

            if len(examples) < 20 and (
                old_route != scores.routing_class
                or old_fit != scores.fit_score
                or old_invite != scores.invite_score
            ):
                examples.append(
                    f"vacancy={vacancy.id} "
                    f"{old_route}/{old_fit}/{old_invite} -> "
                    f"{scores.routing_class}/{scores.fit_score}/{scores.invite_score} "
                    f"{vacancy.company or ''} | {vacancy.title or ''}"
                )

            if not apply:
                continue

            assessment.extraction_json = sanitized.model_dump_json()
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

        print(
            "[CLEAN COVER REPAIR] "
            f"mode={'apply' if apply else 'dry-run'} "
            f"changed={changed} route_changes={route_changes} "
            f"fit_changes={fit_changes} invite_changes={invite_changes}"
        )
        for line in examples:
            print(f"[CLEAN COVER REPAIR] {line}")

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
        help="Persist deterministic removal of unconfirmed pre-apply cover evidence.",
    )
    args = parser.parse_args()
    return repair(apply=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
