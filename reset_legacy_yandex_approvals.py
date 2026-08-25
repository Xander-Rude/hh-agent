from __future__ import annotations

from sqlalchemy import delete, select

from app.db import Application, SessionLocal, Vacancy


def main() -> None:
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Vacancy.source == "yandex",
                Application.status == "approved",
            )
            .order_by(Application.id.asc())
        ).all()

        if not rows:
            print("Legacy Yandex approved applications: 0")
            return

        print(f"Legacy Yandex approved applications: {len(rows)}")
        for application, vacancy in rows:
            print(
                f"DELETE Application ID={application.id} | "
                f"Vacancy ID={vacancy.id} | {vacancy.title}"
            )

        ids = [application.id for application, _ in rows]
        session.execute(
            delete(Application).where(Application.id.in_(ids))
        )
        session.commit()

        print(
            "Готово. Эти вакансии снова появятся в Telegram /new, "
            "а статус approved возникнет только после нажатия «Откликнуться»."
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
