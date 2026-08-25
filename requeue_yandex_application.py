from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import Application, Evaluation, SessionLocal, Vacancy


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python requeue_yandex_application.py <application_id>")
        raise SystemExit(2)

    try:
        application_id = int(sys.argv[1])
    except ValueError:
        print("[ERROR] application_id должен быть целым числом")
        raise SystemExit(2)

    session = SessionLocal()
    try:
        row = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(Application.id == application_id)
            .limit(1)
        ).first()

        if row is None:
            print(f"[ERROR] Application {application_id} не найдена")
            raise SystemExit(1)

        application, vacancy = row

        print("=" * 80)
        print("YANDEX APPLICATION SAFE REQUEUE")
        print("=" * 80)
        print(f"Application ID: {application.id}")
        print(f"status: {application.status!r}")
        print(f"applied_at: {application.applied_at!r}")
        print(f"Vacancy ID: {vacancy.id}")
        print(f"source: {vacancy.source!r}")
        print(f"title: {vacancy.title}")

        if (vacancy.source or "").strip().lower() != "yandex":
            print("[BLOCK] Это не Yandex vacancy — статус не меняю")
            raise SystemExit(1)

        if application.status != "manual_required":
            print("[BLOCK] Requeue разрешён только из manual_required")
            raise SystemExit(1)

        if application.applied_at is not None:
            print("[BLOCK] applied_at уже установлен — повторная отправка запрещена")
            raise SystemExit(1)

        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy.id)
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        ).first()

        if evaluation is None:
            print("[BLOCK] Нет Evaluation")
            raise SystemExit(1)

        print(f"Latest Evaluation: id={evaluation.id} decision={evaluation.decision!r} score={evaluation.score}")

        if (evaluation.decision or "").strip().lower() == "reject":
            print("[BLOCK] Latest Evaluation=reject — requeue запрещён")
            raise SystemExit(1)

        cover_letter = (application.cover_letter or evaluation.cover_letter or "").strip()
        resume_key = (
            application.selected_resume_key
            or evaluation.selected_resume_key
            or ""
        ).strip()

        if not cover_letter:
            print("[BLOCK] Нет сопроводительного письма")
            raise SystemExit(1)

        if not resume_key:
            print("[BLOCK] Не выбрано резюме")
            raise SystemExit(1)

        application.status = "approved"
        session.commit()

        print("[OK] status: manual_required -> approved")
        print("[SAFE] applied_at не изменялся; отклик не отправлялся этим скриптом")
    finally:
        session.close()


if __name__ == "__main__":
    main()
