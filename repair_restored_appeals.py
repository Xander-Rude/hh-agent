from __future__ import annotations

import argparse
import json
from datetime import datetime

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy
from app.evaluation_policy import apply_management_policy
from app.models import VacancyEvaluation
from process_vacancies import build_vacancy_text


DEFAULT_SINCE = "2026-09-01"
APPEAL_MODEL_SUFFIX = "+hard-filter-appeal"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Re-apply the current deterministic management policy to vacancies "
            "already restored by hard-filter appeal. No LLM, Pipeline or Apply."
        )
    )
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--vacancy-id", type=int)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist repaired evaluations. Without this flag the command is dry-run only.",
    )
    return parser.parse_args()


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def evaluation_to_result(evaluation: Evaluation) -> VacancyEvaluation:
    return VacancyEvaluation(
        score=int(evaluation.score or 0),
        decision=evaluation.decision,
        role_match=int(evaluation.role_match or 0),
        seniority_match=int(evaluation.seniority_match or 0),
        domain_match=int(evaluation.domain_match or 0),
        responsibility_match=int(evaluation.responsibility_match or 0),
        must_have_missing=_json_list(evaluation.must_have_missing),
        nice_to_have_missing=_json_list(evaluation.nice_to_have_missing),
        strengths=_json_list(evaluation.strengths),
        gaps=_json_list(evaluation.gaps),
        red_flags=_json_list(evaluation.red_flags),
        summary=evaluation.summary or "",
        recommendation=evaluation.recommendation or "",
        cover_letter=evaluation.cover_letter or "",
    )


def result_snapshot(result: VacancyEvaluation) -> dict:
    return {
        "score": result.score,
        "decision": result.decision,
        "role_match": result.role_match,
        "seniority_match": result.seniority_match,
        "domain_match": result.domain_match,
        "responsibility_match": result.responsibility_match,
        "must_have_missing": list(result.must_have_missing or []),
        "nice_to_have_missing": list(result.nice_to_have_missing or []),
        "strengths": list(result.strengths or []),
        "gaps": list(result.gaps or []),
        "red_flags": list(result.red_flags or []),
        "summary": result.summary,
        "recommendation": result.recommendation,
        "cover_letter": result.cover_letter,
    }


def repair_result(
    evaluation: Evaluation,
    *,
    resume: str,
    vacancy_text: str,
) -> tuple[VacancyEvaluation, dict, dict]:
    result = evaluation_to_result(evaluation)
    before = result_snapshot(result)
    result = apply_management_policy(
        result,
        resume=resume,
        vacancy=vacancy_text,
    )
    after = result_snapshot(result)
    return result, before, after


def load_candidates(
    session,
    *,
    since: datetime,
    limit: int | None = None,
    vacancy_id: int | None = None,
):
    latest_id = (
        select(Evaluation.id)
        .where(Evaluation.vacancy_id == Vacancy.id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
        .correlate(Vacancy)
        .scalar_subquery()
    )

    stmt = (
        select(Vacancy, Evaluation)
        .join(Evaluation, Evaluation.id == latest_id)
        .where(Evaluation.created_at >= since)
        .where(Evaluation.model.endswith(APPEAL_MODEL_SUFFIX))
        .order_by(Evaluation.created_at.asc(), Evaluation.id.asc())
    )

    if vacancy_id is not None:
        stmt = stmt.where(Vacancy.id == vacancy_id)

    if limit:
        stmt = stmt.limit(max(1, limit))

    return session.execute(stmt).all()


def persist_repaired(
    session,
    *,
    source: Evaluation,
    result: VacancyEvaluation,
) -> None:
    session.add(
        Evaluation(
            vacancy_id=source.vacancy_id,
            score=result.score,
            decision=result.decision,
            role_match=result.role_match,
            seniority_match=result.seniority_match,
            domain_match=result.domain_match,
            responsibility_match=result.responsibility_match,
            must_have_missing=json.dumps(result.must_have_missing, ensure_ascii=False),
            nice_to_have_missing=json.dumps(result.nice_to_have_missing, ensure_ascii=False),
            strengths=json.dumps(result.strengths, ensure_ascii=False),
            gaps=json.dumps(result.gaps, ensure_ascii=False),
            red_flags=json.dumps(result.red_flags, ensure_ascii=False),
            summary=result.summary,
            recommendation=result.recommendation,
            cover_letter=result.cover_letter,
            selected_resume_key=source.selected_resume_key,
            selected_resume_title=source.selected_resume_title,
            selected_resume_id=source.selected_resume_id,
            selected_resume_score=source.selected_resume_score,
            # Keep the suffix so Telegram appeal delivery continues to recognise
            # the latest evaluation as a restored hard-filter vacancy.
            model=source.model,
        )
    )
    session.commit()


def main() -> None:
    args = parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d")

    with open("data/resume.txt", "r", encoding="utf-8") as file:
        resume = file.read()

    session = SessionLocal()
    try:
        rows = load_candidates(
            session,
            since=since,
            limit=args.limit,
            vacancy_id=args.vacancy_id,
        )

        mode = "WRITE" if args.write else "DRY-RUN"
        print(f"Restored appeal evaluations: {len(rows)} | mode={mode}")

        changed = 0
        unchanged = 0
        written = 0

        for vacancy, evaluation in rows:
            vacancy_text = build_vacancy_text(vacancy)
            result, before, after = repair_result(
                evaluation,
                resume=resume,
                vacancy_text=vacancy_text,
            )

            if before == after:
                unchanged += 1
                continue

            changed += 1
            print(
                f"[CHANGE] vacancy={vacancy.id} | {vacancy.title} | "
                f"decision {before['decision']} -> {after['decision']} | "
                f"score {before['score']} -> {after['score']} | "
                f"red_flags {len(before['red_flags'])} -> {len(after['red_flags'])} | "
                f"must_have {len(before['must_have_missing'])} -> {len(after['must_have_missing'])}"
            )

            if args.write:
                persist_repaired(
                    session,
                    source=evaluation,
                    result=result,
                )
                written += 1

        print("=" * 72)
        print(f"Changed: {changed}")
        print(f"Unchanged: {unchanged}")
        print(f"Written: {written}")
        if not args.write:
            print("Dry-run only. Re-run with --write to persist these repairs.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
