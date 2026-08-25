from __future__ import annotations

from sqlalchemy import select

from app.db import Application, Evaluation, SessionLocal, Vacancy


def latest_evaluation(session, vacancy_id: int) -> Evaluation | None:
    return session.scalars(
        select(Evaluation)
        .where(Evaluation.vacancy_id == vacancy_id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    ).first()


def existing_application(session, vacancy_id: int) -> Application | None:
    return session.scalars(
        select(Application)
        .where(Application.vacancy_id == vacancy_id)
        .order_by(Application.created_at.asc(), Application.id.asc())
        .limit(1)
    ).first()


def main() -> int:
    session = SessionLocal()

    created = 0
    skipped_reject = 0
    skipped_missing_data = 0
    skipped_existing = 0

    try:
        vacancies = session.scalars(
            select(Vacancy)
            .where(Vacancy.source == "yandex")
            .order_by(Vacancy.id.asc())
        ).all()

        print("=" * 80)
        print("YANDEX APPLICATION BACKFILL")
        print("=" * 80)
        print(f"Yandex-вакансий в БД: {len(vacancies)}")

        for vacancy in vacancies:
            evaluation = latest_evaluation(session, vacancy.id)
            if evaluation is None:
                continue

            existing = existing_application(session, vacancy.id)
            if existing is not None:
                skipped_existing += 1
                print(
                    f"[SKIP] vacancy={vacancy.id}: Application уже есть "
                    f"id={existing.id} status={existing.status}"
                )
                continue

            decision = (evaluation.decision or "").strip().lower()
            if decision == "reject":
                skipped_reject += 1
                continue

            cover_letter = (evaluation.cover_letter or "").strip()
            resume_key = (evaluation.selected_resume_key or "").strip()

            if not cover_letter or not resume_key:
                skipped_missing_data += 1
                print(
                    f"[SKIP] vacancy={vacancy.id}: "
                    f"cover_letter={bool(cover_letter)} resume_key={bool(resume_key)}"
                )
                continue

            application = Application(
                vacancy_id=vacancy.id,
                status="approved",
                cover_letter=cover_letter,
                selected_resume_key=evaluation.selected_resume_key,
                selected_resume_title=evaluation.selected_resume_title,
                selected_resume_id=evaluation.selected_resume_id,
                selected_resume_score=evaluation.selected_resume_score,
            )
            session.add(application)
            session.flush()

            created += 1
            print(
                f"[CREATE] Application ID={application.id} | "
                f"vacancy={vacancy.id} | {vacancy.title}"
            )

        session.commit()

        print("\n" + "=" * 80)
        print("BACKFILL DONE")
        print(f"created={created}")
        print(f"skipped_existing={skipped_existing}")
        print(f"skipped_reject={skipped_reject}")
        print(f"skipped_missing_data={skipped_missing_data}")
        print("=" * 80)
        return 0

    except Exception as exc:
        session.rollback()
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
