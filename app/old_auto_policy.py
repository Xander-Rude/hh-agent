from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clean_live_guard import current_clean_assessment
from app.db import Evaluation


OLD_AUTO_MIN_SCORE = max(
    0,
    min(100, int(os.getenv("HH_OLD_AUTO_MIN_SCORE", "80"))),
)


@dataclass(frozen=True)
class OldAutoEligibility:
    eligible: bool
    reason: str
    score: int | None = None


def latest_evaluation(
    session: Session,
    vacancy_id: int,
) -> Evaluation | None:
    return session.scalar(
        select(Evaluation)
        .where(Evaluation.vacancy_id == vacancy_id)
        .order_by(Evaluation.id.desc())
        .limit(1)
    )


def old_auto_eligibility(
    session: Session,
    vacancy_id: int,
    *,
    min_score: int | None = None,
) -> OldAutoEligibility:
    """Broad-but-relevant OLD auto-apply gate.

    OLD requires both layers:
    1. legacy score at or above the broad threshold;
    2. a current semantic shadow route that is not SKIP/HOLD.

    CLEAN-only requirement gaps can still route to OLD_REVIEW, so OLD remains
    intentionally broader than CLEAN without falling back to full coverage.
    """
    threshold = OLD_AUTO_MIN_SCORE if min_score is None else int(min_score)

    evaluation = latest_evaluation(session, vacancy_id)
    if evaluation is None:
        return OldAutoEligibility(
            eligible=False,
            reason="missing_evaluation",
            score=None,
        )

    score = int(evaluation.score or 0)
    if score < threshold:
        return OldAutoEligibility(
            eligible=False,
            reason=f"score_below_minimum:{score}<{threshold}",
            score=score,
        )

    assessment = current_clean_assessment(session, vacancy_id)
    if assessment is None:
        return OldAutoEligibility(
            eligible=False,
            reason="missing_current_semantic_assessment",
            score=score,
        )

    route = str(assessment.routing_class or "").strip() or "NONE"
    if route in {"SKIP", "HOLD"}:
        return OldAutoEligibility(
            eligible=False,
            reason=f"routing_class={route}",
            score=score,
        )

    return OldAutoEligibility(
        eligible=True,
        reason=f"score={score}>={threshold};routing_class={route}",
        score=score,
    )
