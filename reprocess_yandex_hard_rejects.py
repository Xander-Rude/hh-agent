from __future__ import annotations

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy


def main() -> int:
    session = SessionLocal()
    try:
        vacancies = session.scalars(
            select(Vacancy)
            .where(Vacancy.source == "yandex")
            .order_by(Vacancy.id.asc())
        ).all()

        reset = 0
        skipped = 0

        print("=" * 80)
        print("YANDEX HARD-REJECT REPROCESS PREP")
        print("=" * 80)

        for vacancy in vacancies:
            latest = session.scalars(
                select(Evaluation)
                .where(Evaluation.vacancy_id == vacancy.id)
                .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
                .limit(1)
            ).first()

            if latest is None:
                skipped += 1
                continue

            model = (latest.model or "").strip().lower()
            decision = (latest.decision or "").strip().lower()

            if decision != "reject" or not model.startswith("hard-filter/"):
                skipped += 1
                continue

            vacancy.processed = False
            reset += 1
            print(f"[RESET] Vacancy ID={vacancy.id} | {vacancy.title}")

        session.commit()

        print("\n" + "=" * 80)
        print("DONE")
        print(f"reset={reset}")
        print(f"skipped={skipped}")
        print("История Evaluation не удалялась.")
        print("Теперь запустите process_vacancies.py.")
        print("=" * 80)

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
