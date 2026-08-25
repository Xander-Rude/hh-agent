from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db import Application, Evaluation, SessionLocal, Vacancy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("application_id", type=int)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        application = session.get(Application, args.application_id)
        if application is None:
            print(f"Application ID={args.application_id}: NOT FOUND")
            return 2

        vacancy = session.get(Vacancy, application.vacancy_id)

        print("=" * 80)
        print("APPLICATION DIAGNOSTIC")
        print("=" * 80)
        print(f"Application ID: {application.id}")
        print(f"status: {application.status!r}")
        print(f"vacancy_id: {application.vacancy_id}")
        print(f"cover_letter_empty: {not bool((application.cover_letter or '').strip())}")
        print(f"selected_resume_key: {application.selected_resume_key!r}")
        print(f"selected_resume_title: {application.selected_resume_title!r}")
        print(f"selected_resume_id: {application.selected_resume_id!r}")
        print(f"selected_resume_score: {application.selected_resume_score!r}")
        print(f"applied_at: {application.applied_at!r}")

        if vacancy is None:
            print("Vacancy: NOT FOUND")
            return 3

        print("-" * 80)
        print(f"Vacancy ID: {vacancy.id}")
        print(f"source: {vacancy.source!r}")
        print(f"external_id: {vacancy.external_id!r}")
        print(f"title: {vacancy.title}")
        print(f"company: {vacancy.company!r}")
        print(f"url: {vacancy.url}")
        print(f"processed: {vacancy.processed!r}")

        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy.id)
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        ).first()

        print("-" * 80)
        if evaluation is None:
            print("Latest Evaluation: NOT FOUND")
        else:
            print(f"Latest Evaluation ID: {evaluation.id}")
            print(f"decision: {evaluation.decision!r}")
            print(f"score: {evaluation.score!r}")
            print(f"cover_letter_empty: {not bool((evaluation.cover_letter or '').strip())}")
            print(f"selected_resume_key: {evaluation.selected_resume_key!r}")
            print(f"selected_resume_title: {evaluation.selected_resume_title!r}")
            print(f"selected_resume_score: {evaluation.selected_resume_score!r}")

        print("=" * 80)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
