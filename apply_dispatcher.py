from __future__ import annotations

import os

from sqlalchemy import or_, select

import apply_worker as hh_worker
import vk_apply_worker
import yandex_apply_worker
from app.db import Application, SessionLocal, Vacancy
from hh_session_guard import check_hh_session


YANDEX_APPLY_LIVE = os.getenv("YANDEX_APPLY_LIVE", "false").lower() == "true"
YANDEX_APPLY_APPLICATION_ID = os.getenv("YANDEX_APPLY_APPLICATION_ID", "").strip()
VK_APPLY_LIVE = os.getenv("VK_APPLY_LIVE", "false").lower() == "true"
VK_APPLY_APPLICATION_ID = os.getenv("VK_APPLY_APPLICATION_ID", "").strip()
DISPATCH_HH = os.getenv("APPLY_DISPATCH_HH", "true").lower() == "true"


# HH periodically changes the wording shown after a successful response.
# Keep the legacy worker conservative, but recognize the stable variants
# currently seen in the HH UI instead of sending successful clicks to
# manual_required merely because the exact confirmation text changed.
EXTRA_HH_SUCCESS_MARKERS = [
    "отклик успешно отправлен",
    "ваш отклик отправлен",
    "ваш отклик успешно отправлен",
    "отклик на вакансию отправлен",
    "отклик отправлен работодателю",
    "резюме успешно отправлено",
]

for marker in EXTRA_HH_SUCCESS_MARKERS:
    if marker not in hh_worker.SUCCESS_MARKERS:
        hh_worker.SUCCESS_MARKERS.append(marker)
    if marker not in hh_worker.ALREADY_APPLIED_MARKERS:
        hh_worker.ALREADY_APPLIED_MARKERS.append(marker)


# HH has two legitimate cover-letter flows:
# 1) the response form is opened before the application is sent;
# 2) for vacancies where a cover letter is optional, the first click may send
#    the application immediately, after which HH shows
#    "Приложить сопроводительное письмо" on the delivered-response page.
#
# apply_worker.py handles (1) natively. The compatibility layer below lets the
# same conservative worker continue through (2) instead of returning applied
# immediately and silently leaving the application without our letter.
_HH_ORIGINAL_ALREADY_APPLIED = hh_worker.already_applied
_HH_ORIGINAL_FIND_COVER_LETTER_TRIGGER = hh_worker.find_cover_letter_trigger
_HH_ORIGINAL_PROCESS_APPLICATION = hh_worker.process_application
_HH_OPTIONAL_LETTER_STATE = {
    "initial_check_done": False,
    "already_applied_on_open": False,
    "instant_apply_detected": False,
}


def _reset_hh_optional_letter_state() -> None:
    _HH_OPTIONAL_LETTER_STATE["initial_check_done"] = False
    _HH_OPTIONAL_LETTER_STATE["already_applied_on_open"] = False
    _HH_OPTIONAL_LETTER_STATE["instant_apply_detected"] = False


def _hh_already_applied_with_optional_letter(page) -> bool:
    is_applied = _HH_ORIGINAL_ALREADY_APPLIED(page)

    # First check happens immediately after opening the vacancy. Preserve the
    # normal "already applied earlier" behaviour in that case.
    if not _HH_OPTIONAL_LETTER_STATE["initial_check_done"]:
        _HH_OPTIONAL_LETTER_STATE["initial_check_done"] = True
        _HH_OPTIONAL_LETTER_STATE["already_applied_on_open"] = is_applied
        return is_applied

    # If the vacancy was not applied on page open, but became applied right
    # after click_initial_apply(), HH used its instant-apply flow. Do not let
    # the legacy worker return applied yet: it still has to attach our letter.
    if (
        is_applied
        and not _HH_OPTIONAL_LETTER_STATE["already_applied_on_open"]
        and not _HH_OPTIONAL_LETTER_STATE["instant_apply_detected"]
    ):
        _HH_OPTIONAL_LETTER_STATE["instant_apply_detected"] = True
        print(
            "[STEP] HH отправил отклик сразу. "
            "Пробую приложить сопроводительное после отклика..."
        )
        return False

    return is_applied


def _hh_find_cover_letter_trigger_with_attach(page):
    trigger = _HH_ORIGINAL_FIND_COVER_LETTER_TRIGGER(page)
    if trigger is not None:
        return trigger

    # Current HH wording for an optional letter after an instant application.
    texts = [
        "Приложить сопроводительное письмо",
        "Приложить сопроводительное",
        "Добавить сопроводительное к отклику",
    ]

    for role in ("button", "link"):
        for text in texts:
            locator = page.get_by_role(
                role,
                name=text,
                exact=False,
            )

            try:
                count = locator.count()
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue

    return None


def _hh_process_application_with_optional_letter(page, vacancy, application):
    _reset_hh_optional_letter_state()
    return _HH_ORIGINAL_PROCESS_APPLICATION(page, vacancy, application)


hh_worker.already_applied = _hh_already_applied_with_optional_letter
hh_worker.find_cover_letter_trigger = _hh_find_cover_letter_trigger_with_attach
hh_worker.process_application = _hh_process_application_with_optional_letter


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


def _load_approved_queue(
    *,
    source: str,
    target_application_id: str,
    max_per_run: int,
):
    """Возвращает вручную подтверждённые applications конкретного источника.

    Application.status=approved является финальным разрешением пользователя
    на отправку. Старое решение Evaluation (apply/review/reject) после ручного
    подтверждения больше не может заблокировать отклик.
    """
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
            query = query.limit(max_per_run)

        rows = session.execute(query).all()
        result = []

        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))

        return result
    finally:
        session.close()


def load_yandex_queue_approved():
    return _load_approved_queue(
        source="yandex",
        target_application_id=YANDEX_APPLY_APPLICATION_ID,
        max_per_run=yandex_apply_worker.MAX_PER_RUN,
    )


def load_vk_queue_approved():
    return _load_approved_queue(
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
    print(f"{label} approved в очереди: {len(queue)}")

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


def _run_hh_source() -> None:
    if not DISPATCH_HH:
        print("HH dispatcher отключён через APPLY_DISPATCH_HH=false")
        return

    queue = load_hh_queue()
    print("\n" + "=" * 80)
    print("Переход к HH queue")
    print("=" * 80)
    print(f"HH approved в очереди: {len(queue)}")

    if not queue:
        print("HH: отправлять нечего.")
        return

    session_status = check_hh_session(headless=hh_worker.HEADLESS)
    if not session_status.authenticated:
        print(
            "[HH AUTH] HH-отклики остановлены: "
            f"{session_status.reason}"
        )
        if session_status.final_url:
            print(f"[HH AUTH] Final URL: {session_status.final_url}")
        print(
            "[HH AUTH] Approved-очередь оставлена без изменений. "
            "После повторного входа следующий Apply run попробует снова."
        )
        return

    original_hh_load_queue = hh_worker.load_queue
    hh_worker.load_queue = lambda: queue
    try:
        hh_worker.main()
    finally:
        hh_worker.load_queue = original_hh_load_queue


def main() -> None:
    print("=" * 80)
    print("APPLICATION DISPATCHER")
    print("HH -> apply_worker.py (source=hh, status=approved)")
    print("Yandex -> yandex_apply_worker.py (source=yandex, status=approved)")
    print("VK -> vk_apply_worker.py (source=vk, status=approved)")
    print("=" * 80)

    _run_hh_source()

    _run_external_source(
        label="Yandex",
        live=YANDEX_APPLY_LIVE,
        target_application_id=YANDEX_APPLY_APPLICATION_ID,
        queue=load_yandex_queue_approved(),
        worker=yandex_apply_worker,
    )

    _run_external_source(
        label="VK",
        live=VK_APPLY_LIVE,
        target_application_id=VK_APPLY_APPLICATION_ID,
        queue=load_vk_queue_approved(),
        worker=vk_apply_worker,
    )


if __name__ == "__main__":
    main()
