from __future__ import annotations

import os

from sqlalchemy import or_, select

import apply_worker as hh_worker
import vk_apply_worker
import yandex_apply_worker
from app.db import Application, Evaluation, SessionLocal, Vacancy


YANDEX_APPLY_LIVE = os.getenv("YANDEX_APPLY_LIVE", "false").lower() == "true"
YANDEX_APPLY_APPLICATION_ID = os.getenv("YANDEX_APPLY_APPLICATION_ID", "").strip()
VK_APPLY_LIVE = os.getenv("VK_APPLY_LIVE", "false").lower() == "true"
VK_APPLY_APPLICATION_ID = os.getenv("VK_APPLY_APPLICATION_ID", "").strip()
DISPATCH_HH = os.getenv("APPLY_DISPATCH_HH", "true").lower() == "true"


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


def _latest_decision(session, vacancy_id: int) -> str:
    decision = session.scalars(
        select(Evaluation.decision)
        .where(Evaluation.vacancy_id == vacancy_id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    ).first()
    return (decision or "").strip().lower()


def _load_guarded_queue(
    *,
    source: str,
    target_application_id: str,
    max_per_run: int,
):
    """Approved queue конкретного источника, только если latest Evaluation=apply."""
    session = SessionLocal()
    try:
        query = (
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Vacancy.source == source,
            )
            .order_by(Application.created_at.asc())
        )

        if target_application_id:
            try:
                target_id = int(target_application_id)
            except ValueError:
                print(
                    f"[ERROR] {source.upper()}_APPLY_APPLICATION_ID должен быть целым числом; "
                    f"{source} worker не запускается."
                )
                return []
            query = query.where(Application.id == target_id).limit(1)
        else:
            query = query.limit(max(max_per_run * 5, 25))

        rows = session.execute(query).all()
        result = []

        for application, vacancy in rows:
            decision = _latest_decision(session, vacancy.id)
            if decision != "apply":
                print(
                    f"[SAFE] {source.upper()} Application ID={application.id} пропущена: "
                    f"latest decision={decision or 'none'} (автоматически разрешён только apply)."
                )
                continue

            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))

            if len(result) >= max_per_run:
                break

        return result
    finally:
        session.close()


def load_yandex_queue_guarded():
    return _load_guarded_queue(
        source="yandex",
        target_application_id=YANDEX_APPLY_APPLICATION_ID,
        max_per_run=yandex_apply_worker.MAX_PER_RUN,
    )


def load_vk_queue_guarded():
    return _load_guarded_queue(
        source="vk",
        target_application_id=VK_APPLY_APPLICATION_ID,
        max_per_run=vk_apply_worker.MAX_PER_RUN,
    )


def _run_external_source(
    *,
    label: str,
    live: bool,
    target_application_id: str,
    queue,
    worker,
) -> None:
    print("\n" + "=" * 80)
    print(f"Переход к {label} queue")
    print("=" * 80)
    print(f"{label} apply в защищённой очереди: {len(queue)}")

    if target_application_id:
        print(f"Target Application ID: {target_application_id}")

    if not queue:
        print(f"{label}: отправлять нечего.")
        return

    if not live:
        print(
            f"[SAFE] {label.upper()}_APPLY_LIVE=false. "
            f"Боевые {label}-отклики НЕ отправляются."
        )
        print("Очередь:")
        for application, vacancy in queue:
            print(
                f"  Application ID={application.id} | "
                f"Vacancy ID={vacancy.id} | {vacancy.title}"
            )
        return

    print(f"[LIVE] {label.upper()}_APPLY_LIVE=true — разрешена финальная отправка {label}.")

    original_load_queue = worker.load_queue
    worker.load_queue = lambda: queue
    try:
        worker.main()
    finally:
        worker.load_queue = original_load_queue


def main() -> None:
    print("=" * 80)
    print("APPLICATION DISPATCHER")
    print("HH -> apply_worker.py (source=hh only)")
    print("Yandex -> yandex_apply_worker.py (source=yandex, latest decision=apply only)")
    print("VK -> vk_apply_worker.py (source=vk, latest decision=apply only)")
    print("=" * 80)

    if DISPATCH_HH:
        original_hh_load_queue = hh_worker.load_queue
        hh_worker.load_queue = load_hh_queue
        try:
            hh_worker.main()
        finally:
            hh_worker.load_queue = original_hh_load_queue
    else:
        print("HH dispatcher отключён через APPLY_DISPATCH_HH=false")

    _run_external_source(
        label="Yandex",
        live=YANDEX_APPLY_LIVE,
        target_application_id=YANDEX_APPLY_APPLICATION_ID,
        queue=load_yandex_queue_guarded(),
        worker=yandex_apply_worker,
    )

    _run_external_source(
        label="VK",
        live=VK_APPLY_LIVE,
        target_application_id=VK_APPLY_APPLICATION_ID,
        queue=load_vk_queue_guarded(),
        worker=vk_apply_worker,
    )


if __name__ == "__main__":
    main()
