from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy
from app.evaluation_grounding import ground_and_decide
from app.evaluation_policy import apply_management_policy
from app.evaluator import VacancyEvaluator
from app.gpu_guard import should_defer_ollama
from app.hard_filter_appeal import (
    HardFilterAppealReviewer,
    appeal_confidence_threshold,
    should_override_hard_reject,
)
from app.hard_filters import apply_hard_filters
from app.llm_resilience import configure_processor_llm_environment, is_transient_ollama_failure
from app.preferences import load_preferences
from process_vacancies import build_vacancy_text

DEFAULT_SINCE = "2026-09-01"
FINAL_MARKERS = ("ниже минимума", "компания в blacklist")


def configure_console_encoding(*, stdout=None, stderr=None) -> None:
    """Use UTF-8 for Windows console output without crashing on Unicode titles."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr

    for stream in (stdout, stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def historical_reason(evaluation: Evaluation) -> str:
    try:
        values = json.loads(evaluation.red_flags or "[]")
        if isinstance(values, list) and values:
            return str(values[0]).strip()
    except Exception:
        pass
    return "Исторический hard-filter reject"


def is_final_reason(reason: str) -> bool:
    text = (reason or "").lower()
    return any(marker in text for marker in FINAL_MARKERS)


def load_candidates(session, since: datetime, limit: int | None = None):
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
        .where(Evaluation.model.like("hard-filter/%"))
        .order_by(Evaluation.created_at.asc(), Evaluation.id.asc())
    )
    if limit:
        stmt = stmt.limit(max(1, limit))
    return session.execute(stmt).all()


def add_confirmed_reject(session, vacancy, old_reason, appeal, model_name):
    session.add(Evaluation(
        vacancy_id=vacancy.id,
        score=0,
        decision="reject",
        role_match=0,
        seniority_match=0,
        domain_match=0,
        responsibility_match=0,
        must_have_missing="[]",
        nice_to_have_missing="[]",
        strengths="[]",
        gaps="[]",
        red_flags=json.dumps([old_reason], ensure_ascii=False),
        summary=(
            "Исторический hard-filter reject перепроверен LLM-апелляцией; "
            f"reject подтверждён ({appeal.confidence:.0%}). {appeal.reason}"
        ),
        recommendation=f"Пропустить. LLM-апелляция: {appeal.reason}",
        cover_letter="",
        model=f"hard-filter+appeal-backfill/{model_name}",
    ))
    session.commit()


def add_restored_evaluation(session, vacancy, result, old_reason, appeal, model_name):
    note = (
        "🛟 Восстановлена исторической LLM-апелляцией "
        f"({appeal.confidence:.0%}). Старый hard-filter: {old_reason}. "
        f"Апелляция: {appeal.reason}"
    )
    result.summary = f"[HARD_FILTER_APPEAL_OVERRIDE] {note}\n{result.summary}"
    result.recommendation = f"{note}\n\n{result.recommendation}"
    session.add(Evaluation(
        vacancy_id=vacancy.id,
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
        model=f"{model_name}+hard-filter-appeal",
    ))
    session.commit()


def main():
    configure_console_encoding()
    args = parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d")
    session = SessionLocal()
    preferences = load_preferences()
    rows = load_candidates(session, since, args.limit)
    print(f"Hard-filter rejects since {args.since}: {len(rows)}")

    if args.dry_run:
        appealable = final = 0
        for vacancy, evaluation in rows:
            reason = historical_reason(evaluation)
            current = apply_hard_filters(
                title=vacancy.title,
                company=vacancy.company,
                description=vacancy.description,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=vacancy.salary_currency,
                preferences=preferences,
            )
            skip = is_final_reason(reason) or (not current.passed and not current.appealable)
            label = "FINAL" if skip else "APPEAL"
            print(f"[{label}] {vacancy.id} | {vacancy.title} | {reason}")
            final += int(skip)
            appealable += int(not skip)
        print(f"Dry-run: appeal={appealable}, final_skip={final}")
        session.close()
        return

    configure_processor_llm_environment()
    evaluator = VacancyEvaluator()
    reviewer = HardFilterAppealReviewer(llm=evaluator.llm)
    threshold = appeal_confidence_threshold()
    model_name = os.getenv("LLM_MODEL", "gemma4:12b")
    with open("data/resume.txt", "r", encoding="utf-8") as file:
        resume = file.read()

    restored = confirmed = final = deferred = errors = 0
    for index, (vacancy, evaluation) in enumerate(rows, 1):
        reason = historical_reason(evaluation)
        print(f"\n[{index}/{len(rows)}] {vacancy.title} | {vacancy.company or '-'}")
        current = apply_hard_filters(
            title=vacancy.title,
            company=vacancy.company,
            description=vacancy.description,
            salary_from=vacancy.salary_from,
            salary_to=vacancy.salary_to,
            salary_currency=vacancy.salary_currency,
            preferences=preferences,
        )
        if is_final_reason(reason) or (not current.passed and not current.appealable):
            print(f"  FINAL REJECT: {reason}")
            final += 1
            continue

        gpu = should_defer_ollama()
        if gpu.defer:
            print(f"  [DEFER] {gpu.reason or 'GPU занят'}")
            deferred += len(rows) - index + 1
            break

        vacancy_text = build_vacancy_text(vacancy)
        code = current.code if not current.passed else "historical_hard_filter"
        try:
            appeal = reviewer.review(
                resume=resume,
                vacancy=vacancy_text,
                preferences=preferences,
                hard_filter_reason=reason,
                hard_filter_code=code,
            )
            print(f"  APPEAL: {appeal.verdict} {appeal.confidence:.0%} | {appeal.reason}")
            if not should_override_hard_reject(appeal, threshold=threshold):
                add_confirmed_reject(session, vacancy, reason, appeal, model_name)
                confirmed += 1
                continue

            result = evaluator.evaluate(resume=resume, vacancy=vacancy_text, preferences=preferences)
            result = ground_and_decide(result, vacancy=vacancy_text)
            result = apply_management_policy(result, resume=resume, vacancy=vacancy_text)
            add_restored_evaluation(session, vacancy, result, reason, appeal, model_name)
            restored += 1
            print(f"  🛟 RESTORED: score={result.score} decision={result.decision}")
        except Exception as exc:
            session.rollback()
            if is_transient_ollama_failure(exc):
                deferred += 1
                print("  [LLM DEFER] transient failure; unchanged for rerun")
            else:
                errors += 1
                print(f"  [ERROR] {type(exc).__name__}: {exc}")

    session.close()
    print("\n" + "=" * 60)
    print(f"Restored: {restored}")
    print(f"Confirmed rejects: {confirmed}")
    print(f"Final rejects skipped: {final}")
    print(f"Deferred: {deferred}")
    print(f"Errors: {errors}")
    print("=" * 60)


if __name__ == "__main__":
    main()
