import json
import os
from dataclasses import dataclass

from app.db import Evaluation, Vacancy


@dataclass(frozen=True)
class HuntEligibility:
    eligible: bool
    score: int
    reason: str


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def evaluate_eligibility(vacancy: Vacancy, evaluation: Evaluation) -> HuntEligibility:
    """Gate expensive research without trusting the aggregate score alone."""
    min_score = int(os.getenv("TARGETED_HUNT_MIN_SCORE", "85"))
    max_red_flags = int(os.getenv("TARGETED_HUNT_MAX_RED_FLAGS", "0"))
    min_role = int(os.getenv("TARGETED_HUNT_MIN_ROLE_MATCH", "80"))
    min_seniority = int(os.getenv("TARGETED_HUNT_MIN_SENIORITY_MATCH", "80"))

    red_flags = _json_list(evaluation.red_flags)
    failures: list[str] = []
    if evaluation.decision == "reject":
        failures.append("evaluation rejected")
    if evaluation.score < min_score:
        failures.append(f"score {evaluation.score} < {min_score}")
    if evaluation.role_match < min_role:
        failures.append(f"role {evaluation.role_match} < {min_role}")
    if evaluation.seniority_match < min_seniority:
        failures.append(f"seniority {evaluation.seniority_match} < {min_seniority}")
    if len(red_flags) > max_red_flags:
        failures.append(f"red_flags {len(red_flags)} > {max_red_flags}")
    if not (vacancy.company or "").strip():
        failures.append("company missing")

    if failures:
        return HuntEligibility(False, evaluation.score, "; ".join(failures))
    return HuntEligibility(True, evaluation.score, "high-value targeted-hunt candidate")
