from __future__ import annotations

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy


def short(value: str | None, limit: int = 500) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def main() -> None:
    session = SessionLocal()
    try:
        vacancies = session.scalars(
            select(Vacancy)
            .where(Vacancy.source == "yandex")
            .order_by(Vacancy.id.asc())
        ).all()

        print("=" * 100)
        print("YANDEX EVALUATIONS DIAGNOSTIC")
        print("=" * 100)
        print(f"Yandex-вакансий: {len(vacancies)}")

        counters = {"reject": 0, "non_reject": 0, "no_evaluation": 0}
        hard_rejects = 0

        for vacancy in vacancies:
            evaluation = session.scalars(
                select(Evaluation)
                .where(Evaluation.vacancy_id == vacancy.id)
                .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
                .limit(1)
            ).first()

            print("\n" + "-" * 100)
            print(f"Vacancy ID: {vacancy.id}")
            print(f"Title: {vacancy.title}")
            print(f"URL: {vacancy.url}")

            if evaluation is None:
                counters["no_evaluation"] += 1
                print("Evaluation: <нет>")
                continue

            decision = (evaluation.decision or "").strip().lower()
            if decision == "reject":
                counters["reject"] += 1
            else:
                counters["non_reject"] += 1

            is_hard = (evaluation.model or "").startswith("hard-filter/")
            if is_hard:
                hard_rejects += 1

            print(f"Evaluation ID: {evaluation.id}")
            print(f"Decision: {evaluation.decision}")
            print(f"Score: {evaluation.score}")
            print(f"Model: {evaluation.model}")
            print(f"Hard filter reject: {is_hard}")
            print(
                "Matches: "
                f"role={evaluation.role_match} "
                f"seniority={evaluation.seniority_match} "
                f"domain={evaluation.domain_match} "
                f"responsibility={evaluation.responsibility_match}"
            )
            print(f"Red flags: {short(evaluation.red_flags)}")
            print(f"Must-have missing: {short(evaluation.must_have_missing)}")
            print(f"Gaps: {short(evaluation.gaps)}")
            print(f"Summary: {short(evaluation.summary, 800)}")
            print(f"Recommendation: {short(evaluation.recommendation, 800)}")
            print(f"Cover letter empty: {not bool((evaluation.cover_letter or '').strip())}")
            print(f"Selected resume key: {evaluation.selected_resume_key!r}")
            print(f"Selected resume score: {evaluation.selected_resume_score!r}")

        print("\n" + "=" * 100)
        print("SUMMARY")
        print("=" * 100)
        print(f"reject={counters['reject']}")
        print(f"non_reject={counters['non_reject']}")
        print(f"no_evaluation={counters['no_evaluation']}")
        print(f"hard_filter_rejects={hard_rejects}")
        print(f"llm_rejects={max(0, counters['reject'] - hard_rejects)}")
        print("=" * 100)
    finally:
        session.close()


if __name__ == "__main__":
    main()
