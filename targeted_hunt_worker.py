from __future__ import annotations

import os

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy
from app.gpu_guard import should_defer_ollama
from app.targeted_hunt.eligibility import evaluate_eligibility
from app.targeted_hunt.models import TargetedHuntCase
from app.targeted_hunt.research import research_case


def discover_candidates(limit: int) -> list[int]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Vacancy, Evaluation)
            .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
            .order_by(Evaluation.created_at.desc())
        ).all()
        selected: list[int] = []
        seen: set[int] = set()
        for vacancy, evaluation in rows:
            if vacancy.id in seen:
                continue
            seen.add(vacancy.id)
            existing = session.scalars(
                select(TargetedHuntCase).where(TargetedHuntCase.vacancy_id == vacancy.id)
            ).first()
            if existing is not None and existing.status not in {"retry"}:
                continue
            if evaluate_eligibility(vacancy, evaluation).eligible:
                selected.append(vacancy.id)
            if len(selected) >= limit:
                break
        return selected


def main() -> None:
    if os.getenv("TARGETED_HUNT_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        print("Targeted Hunt disabled. Set TARGETED_HUNT_ENABLED=true to run research.")
        return
    limit = int(os.getenv("TARGETED_HUNT_MAX_CASES_PER_RUN", "3"))
    vacancy_ids = discover_candidates(limit)
    print(f"Targeted Hunt cases this run: {len(vacancy_ids)}")
    for vacancy_id in vacancy_ids:
        gpu = should_defer_ollama()
        if gpu.warning:
            print(f"  [GPU WARN] {gpu.warning}")
        if gpu.defer:
            print(f"  [GPU DEFER] {gpu.reason or 'GPU занят'}; research оставлен до следующего запуска")
            break
        try:
            outcome = research_case(vacancy_id)
            print(f"  vacancy={vacancy_id} case={outcome.case_id} candidates={outcome.candidates} status={outcome.status}")
        except Exception as exc:
            print(f"  vacancy={vacancy_id} ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
