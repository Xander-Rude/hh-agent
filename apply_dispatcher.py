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


# Production HH currently has more than one post-apply cover-letter layout.
# apply_worker.find_letter_submit() used to require an exact button caption in
# the nearest button-containing ancestor. Application 1338 proved that this is
# too strict: HH exposed the textarea, but the real submit control did not match
# those assumptions. Keep this compatibility layer scoped to dispatcher runs.
_HH_ORIGINAL_FIND_LETTER_SUBMIT = hh_worker.find_letter_submit

_HH_LETTER_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button[data-qa*="letter"]',
    'button[data-qa*="cover-letter"]',
    '[role="button"][data-qa*="letter"]',
    '[role="button"][data-qa*="cover-letter"]',
]

_HH_LETTER_SUBMIT_TEXTS = [
    "Приложить",
    "Добавить",
    "Отправить",
    "Сохранить",
    "Готово",
    "Подтвердить",
]


def _hh_locator_exists(locator) -> bool:
    try:
        return locator.count() > 0
    except Exception:
        return False


def _hh_safe_letter_submit(candidate) -> bool:
    try:
        if not candidate.is_visible() or not candidate.is_enabled():
            return False

        data_qa = (candidate.get_attribute("data-qa") or "").lower()
        text = (candidate.inner_text(timeout=1000) or "").strip().lower()

        # Never fall back to the vacancy-level response control. The response
        # is already sent when this helper runs.
        if "vacancy-response-link" in data_qa:
            return False
        if text in {"откликнуться", "отправить отклик"} and "letter" not in data_qa:
            return False

        return True
    except Exception:
        return False


def _hh_first_safe(scope, selector):
    try:
        candidates = scope.locator(selector)
        count = candidates.count()
    except Exception:
        return None

    for index in range(count):
        candidate = candidates.nth(index)
        if _hh_safe_letter_submit(candidate):
            return candidate

    return None


def _hh_dump_letter_controls(field) -> None:
    """Log nearby post-apply controls so the next HH markup change is diagnosable."""
    try:
        controls = field.evaluate(
            """
            el => {
              let node = el.parentElement;
              for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                const items = Array.from(node.querySelectorAll(
                  'button, input[type="submit"], [role="button"]'
                )).filter(item => {
                  const style = window.getComputedStyle(item);
                  return style.display !== 'none' && style.visibility !== 'hidden';
                }).map(item => ({
                  tag: item.tagName.toLowerCase(),
                  text: (item.innerText || item.value || '').trim().slice(0, 120),
                  type: item.getAttribute('type'),
                  dataQa: item.getAttribute('data-qa'),
                  ariaLabel: item.getAttribute('aria-label'),
                  role: item.getAttribute('role')
                }));
                if (items.length) return items.slice(0, 20);
              }
              return [];
            }
            """
        )
        print(f"[DEBUG] HH post-apply letter controls: {controls}")
    except Exception as exc:
        print(f"[DEBUG] HH post-apply letter controls unavailable: {type(exc).__name__}")


def _hh_find_letter_submit_robust(field):
    # Strongest signal: if the textarea belongs to a form, any enabled submit
    # inside that exact form belongs to the letter operation regardless of HH's
    # current caption.
    form = field.locator("xpath=ancestor::form[1]")
    if _hh_locator_exists(form):
        for selector in _HH_LETTER_SUBMIT_SELECTORS:
            candidate = _hh_first_safe(form, selector)
            if candidate is not None:
                return candidate

        for text in _HH_LETTER_SUBMIT_TEXTS:
            try:
                candidates = form.get_by_role("button", name=text, exact=False)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if _hh_safe_letter_submit(candidate):
                        return candidate
            except Exception:
                continue

    # HH can render the textarea and action button as siblings without a form.
    # Search only the closest relevant container/dialog, not the whole page.
    scopes = [
        field.locator("xpath=ancestor::*[@role='dialog'][1]"),
        field.locator(
            "xpath=ancestor::*[.//textarea and "
            "(.//button or .//input[@type='submit'] or .//*[@role='button'])][1]"
        ),
    ]

    for scope in scopes:
        if not _hh_locator_exists(scope):
            continue

        # Outside a form, prefer letter-specific attributes first.
        for selector in _HH_LETTER_SUBMIT_SELECTORS[2:]:
            candidate = _hh_first_safe(scope, selector)
            if candidate is not None:
                return candidate

        for text in _HH_LETTER_SUBMIT_TEXTS:
            try:
                candidates = scope.get_by_role("button", name=text, exact=False)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if _hh_safe_letter_submit(candidate):
                        return candidate
            except Exception:
                continue

    _hh_dump_letter_controls(field)
    return None


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
    original_find_letter_submit = hh_worker.find_letter_submit
    hh_worker.load_queue = lambda: queue
    hh_worker.find_letter_submit = _hh_find_letter_submit_robust
    try:
        hh_worker.main()
    finally:
        hh_worker.load_queue = original_hh_load_queue
        hh_worker.find_letter_submit = original_find_letter_submit


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
