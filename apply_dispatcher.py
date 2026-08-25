from __future__ import annotations

from sqlalchemy import or_, select

import apply_worker as hh_worker
import yandex_apply_worker
from app.db import Application, SessionLocal, Vacancy


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


def main() -> None:
    print("=" * 80)
    print("APPLICATION DISPATCHER")
    print("HH -> apply_worker.py (source=hh only)")
    print("Yandex -> yandex_apply_worker.py (source=yandex only)")
    print("=" * 80)

    # Legacy HH worker оставляем без переписывания, но жёстко ограничиваем
    # его очередь здесь. Его process_application остаётся прежним.
    original_load_queue = hh_worker.load_queue
    hh_worker.load_queue = load_hh_queue
    try:
        hh_worker.main()
    finally:
        hh_worker.load_queue = original_load_queue

    print("\n" + "=" * 80)
    print("Переход к Yandex queue")
    print("=" * 80)
    yandex_apply_worker.main()


if __name__ == "__main__":
    main()
