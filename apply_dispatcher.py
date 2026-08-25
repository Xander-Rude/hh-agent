from __future__ import annotations

import os

from sqlalchemy import or_, select

import apply_worker as hh_worker
import yandex_apply_worker
from app.db import Application, SessionLocal, Vacancy


YANDEX_APPLY_LIVE = os.getenv("YANDEX_APPLY_LIVE", "false").lower() == "true"
YANDEX_APPLY_APPLICATION_ID = os.getenv("YANDEX_APPLY_APPLICATION_ID", "").strip()


def load_hh_queue():
    """Возвращает только HH applications для legacy HH worker.

    source IS NULL оставлен как обратная совместимость со старыми HH-вакансиями,
    созданными до миграции поля source.
    """
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                or_(Vacancy.source == "hh", Vacancy.source.is_(None)),
            )
            .order_by(Application.created_at.asc())
            .limit(hh_worker.MAX_PER_RUN)
        ).all()

        result = []
        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))
        return result
    finally:
        session.close()


def load_yandex_queue_guarded():
    """Возвращает только Yandex approved и при необходимости одну Application.

    При заданном YANDEX_APPLY_APPLICATION_ID делаем прямой запрос по ID,
    чтобы целевая заявка не потерялась из-за MAX_PER_RUN в worker queue.
    """
    if not YANDEX_APPLY_APPLICATION_ID:
        return yandex_apply_worker.load_queue()

    try:
        target_id = int(YANDEX_APPLY_APPLICATION_ID)
    except ValueError:
        print(
            "[ERROR] YANDEX_APPLY_APPLICATION_ID должен быть целым числом; "
            "Yandex worker не запускается."
        )
        return []

    session = SessionLocal()
    try:
        row = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.id == target_id,
                Application.status == "approved",
                Vacancy.source == "yandex",
            )
            .limit(1)
        ).first()

        if row is None:
            return []

        application, vacancy = row
        session.expunge(application)
        session.expunge(vacancy)
        return [(application, vacancy)]
    finally:
        session.close()


def main() -> None:
    print("=" * 80)
    print("APPLICATION DISPATCHER")
    print("HH -> apply_worker.py (source=hh only)")
    print("Yandex -> yandex_apply_worker.py (source=yandex only)")
    print("=" * 80)

    # Legacy HH worker оставляем без переписывания, но жёстко ограничиваем
    # его очередь здесь. Его process_application остаётся прежним.
    original_hh_load_queue = hh_worker.load_queue
    hh_worker.load_queue = load_hh_queue
    try:
        hh_worker.main()
    finally:
        hh_worker.load_queue = original_hh_load_queue

    print("\n" + "=" * 80)
    print("Переход к Yandex queue")
    print("=" * 80)

    guarded_queue = load_yandex_queue_guarded()
    print(f"Yandex approved в защищённой очереди: {len(guarded_queue)}")

    if YANDEX_APPLY_APPLICATION_ID:
        print(f"Target Application ID: {YANDEX_APPLY_APPLICATION_ID}")

    if not guarded_queue:
        print("Yandex: отправлять нечего.")
        return

    if not YANDEX_APPLY_LIVE:
        print(
            "[SAFE] YANDEX_APPLY_LIVE=false. "
            "Боевые Yandex-отклики НЕ отправляются."
        )
        print("Очередь:")
        for application, vacancy in guarded_queue:
            print(
                f"  Application ID={application.id} | "
                f"Vacancy ID={vacancy.id} | {vacancy.title}"
            )
        return

    print("[LIVE] YANDEX_APPLY_LIVE=true — разрешена финальная отправка Yandex.")

    original_yandex_load_queue = yandex_apply_worker.load_queue
    yandex_apply_worker.load_queue = lambda: guarded_queue
    try:
        yandex_apply_worker.main()
    finally:
        yandex_apply_worker.load_queue = original_yandex_load_queue


if __name__ == "__main__":
    main()
