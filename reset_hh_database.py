from __future__ import annotations

from sqlalchemy import delete, text

from app.db import (
    Application,
    Evaluation,
    SessionLocal,
    Vacancy,
    engine,
)


def main() -> None:
    print("=" * 70)
    print("HH AGENT — RESET DATABASE DATA")
    print("=" * 70)
    print(
        "Будут удалены:\n"
        "- все ранее скачанные вакансии\n"
        "- все результаты скоринга\n"
        "- все сопроводительные письма/заявки\n"
        "\nСтруктура БД и настройки останутся."
    )
    print()

    session = SessionLocal()

    try:
        applications = session.query(Application).count()
        evaluations = session.query(Evaluation).count()
        vacancies = session.query(Vacancy).count()

        print(
            f"Сейчас в БД:\n"
            f"  applications: {applications}\n"
            f"  evaluations:  {evaluations}\n"
            f"  vacancies:    {vacancies}"
        )
        print()

        confirm = input(
            'Для очистки введи ровно: RESET\n> '
        ).strip()

        if confirm != "RESET":
            print("Отмена. База не изменена.")
            return

        # Delete children first, then parents.
        session.execute(
            delete(Application)
        )
        session.execute(
            delete(Evaluation)
        )
        session.execute(
            delete(Vacancy)
        )

        session.commit()

        # Reset AUTOINCREMENT counters if sqlite_sequence exists.
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM sqlite_sequence "
                        "WHERE name IN "
                        "('applications', 'evaluations', 'vacancies')"
                    )
                )
        except Exception:
            # Safe to ignore: sqlite_sequence may not exist in some DBs.
            pass

        # Compact SQLite file after deletion.
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("VACUUM")
                )
        except Exception as exc:
            print(
                "[WARN] VACUUM не выполнен: "
                f"{type(exc).__name__}: {exc}"
            )

        print()
        print("Готово.")
        print(
            "Удалены все вакансии, оценки и заявки/сопроводительные."
        )
        print(
            "Следующий hh_collect.py начнёт собирать вакансии с чистого листа."
        )

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    main()
