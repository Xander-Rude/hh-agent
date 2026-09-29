from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clean_live_guard import _absolute_veto_reason, current_clean_assessment
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

    OLD is intentionally wider than CLEAN, but it must not auto-apply to
    obvious semantic mismatches. A current CLEAN SKIP or deterministic hard
    veto always wins. Remaining vacancies need a legacy score >= threshold.
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

    veto_reason = _absolute_veto_reason(session, vacancy_id)
    if veto_reason is not None:
        return OldAutoEligibility(
            eligible=False,
            reason=veto_reason,
            score=score,
        )

    assessment = current_clean_assessment(session, vacancy_id)
    if assessment is not None:
        route = str(assessment.routing_class or "").strip()
        if route == "SKIP":
            return OldAutoEligibility(
                eligible=False,
                reason="routing_class=SKIP",
                score=score,
            )

    return OldAutoEligibility(
        eligible=True,
        reason=f"score={score}>={threshold}",
        score=score,
    )
