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


# Production HH has more than one post-apply cover-letter layout. The first
# instant-apply click can submit the resume before the optional letter UI opens.
# Keep compatibility logic scoped to dispatcher runs so the legacy worker stays
# conservative outside the production dispatcher path.
_HH_ORIGINAL_FIND_LETTER_SUBMIT = hh_worker.find_letter_submit
_HH_ORIGINAL_ATTACH_POST_APPLY_COVER_LETTER = hh_worker.attach_post_apply_cover_letter

_HH_STRICT_COVER_LETTER_FIELD_SELECTORS = [
    'textarea[data-qa*="vacancy-response-letter"]',
    'textarea[data-qa*="cover-letter"]',
    'textarea[name*="letter"]',
]

_HH_POST_APPLY_FIELD_ATTR = "data-hh-agent-post-apply-letter"
_HH_PREEXISTING_TEXTAREA_ATTR = "data-hh-agent-preexisting-textarea"

_HH_LETTER_SPECIFIC_SUBMIT_SELECTORS = [
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


def _hh_first_visible(scope, selector):
    try:
        candidates = scope.locator(selector)
        count = candidates.count()
    except Exception:
        return None

    for index in range(count):
        candidate = candidates.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue

    return None


def _hh_control_metadata(control) -> dict:
    try:
        return control.evaluate(
            """
            el => ({
              tag: el.tagName.toLowerCase(),
              text: (el.innerText || el.value || '').trim().slice(0, 120),
              type: el.getAttribute('type'),
              dataQa: el.getAttribute('data-qa'),
              name: el.getAttribute('name'),
              placeholder: el.getAttribute('placeholder'),
              ariaLabel: el.getAttribute('aria-label'),
              role: el.getAttribute('role')
            })
            """
        )
    except Exception:
        return {"unavailable": True}


def _hh_tag_post_apply_field(field, reason: str):
    try:
        field.set_attribute(_HH_POST_APPLY_FIELD_ATTR, "1")
    except Exception:
        pass
    print(
        f"[DEBUG] HH post-apply letter field selected ({reason}): "
        f"{_hh_control_metadata(field)}"
    )
    return field


def _hh_dump_visible_textareas(page) -> None:
    try:
        textareas = page.locator("textarea").evaluate_all(
            """
            els => els.filter(el => {
              const style = window.getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
            }).map(el => ({
              dataQa: el.getAttribute('data-qa'),
              name: el.getAttribute('name'),
              placeholder: el.getAttribute('placeholder'),
              ariaLabel: el.getAttribute('aria-label'),
              postApply: el.getAttribute('data-hh-agent-post-apply-letter'),
              preexisting: el.getAttribute('data-hh-agent-preexisting-textarea')
            })).slice(0, 20)
            """
        )
        print(f"[DEBUG] HH visible textareas after instant apply: {textareas}")
    except Exception as exc:
        print(f"[DEBUG] HH textarea dump unavailable: {type(exc).__name__}")


def _hh_find_post_apply_cover_letter_field(page):
    """Return only a textarea attributable to the post-apply letter UI.

    Application 1372 proved that using the legacy catch-all ``textarea``
    selector after instant apply is unsafe: any unrelated visible textarea can
    accept the generated text and make the subsequent submit search operate in
    the wrong container. Prefer letter-specific attributes; otherwise click the
    explicit cover-letter trigger and accept only a textarea that appears after
    that click.
    """
    tagged = _hh_first_visible(
        page,
        f'textarea[{_HH_POST_APPLY_FIELD_ATTR}="1"]',
    )
    if tagged is not None:
        return tagged

    for selector in _HH_STRICT_COVER_LETTER_FIELD_SELECTORS:
        field = _hh_first_visible(page, selector)
        if field is not None:
            return _hh_tag_post_apply_field(field, f"strict:{selector}")

    try:
        page.locator("textarea").evaluate_all(
            """
            els => els.forEach(el => {
              const style = window.getComputedStyle(el);
              const visible = style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
              if (visible && el.getAttribute('data-hh-agent-post-apply-letter') !== '1') {
                el.setAttribute('data-hh-agent-preexisting-textarea', '1');
              }
            })
            """
        )
    except Exception:
        pass

    trigger = hh_worker.find_cover_letter_trigger(page)
    if trigger is None:
        _hh_dump_visible_textareas(page)
        return None

    print(
        "[DEBUG] HH post-apply cover-letter trigger selected: "
        f"{_hh_control_metadata(trigger)}"
    )

    try:
        trigger.click()
        page.wait_for_timeout(800)
    except Exception as exc:
        print(f"[DEBUG] HH cover-letter trigger click failed: {type(exc).__name__}")
        return None

    for selector in _HH_STRICT_COVER_LETTER_FIELD_SELECTORS:
        field = _hh_first_visible(page, selector)
        if field is not None:
            return _hh_tag_post_apply_field(field, f"after-trigger:{selector}")

    try:
        candidates = page.locator(
            f'textarea:not([{_HH_PREEXISTING_TEXTAREA_ATTR}="1"])'
        )
        visible = []
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                visible.append(candidate)
        if len(visible) == 1:
            return _hh_tag_post_apply_field(visible[0], "new-after-trigger")
    except Exception:
        pass

    try:
        candidates = page.locator('[role="dialog"] textarea')
        visible = []
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                visible.append(candidate)
        if len(visible) == 1:
            return _hh_tag_post_apply_field(visible[0], "single-dialog-textarea")
    except Exception:
        pass

    _hh_dump_visible_textareas(page)
    return None


def _hh_post_apply_form_still_unsent(field, submit, cover_letter: str) -> bool:
    try:
        field_visible = field.is_visible()
        value_matches = field.input_value(timeout=2000).strip() == cover_letter
        submit_visible = submit.is_visible()
        submit_enabled = submit.is_enabled()
    except Exception:
        field_visible = False
        value_matches = False
        submit_visible = False
        submit_enabled = False

    print(
        "[DEBUG] HH letter form after submit: "
        f"field_visible={field_visible} value_matches={value_matches} "
        f"submit_visible={submit_visible} submit_enabled={submit_enabled}"
    )
    return field_visible and value_matches and submit_visible and submit_enabled


def _hh_resync_letter_field_for_retry(field, cover_letter: str) -> bool:
    """Replay the text through real keyboard events before the one safe retry."""
    try:
        field.click(timeout=2000)
        field.fill("")
        field.press_sequentially(cover_letter, delay=0)
        field.press("Tab", timeout=2000)
        value_matches = field.input_value(timeout=2000).strip() == cover_letter
        print(
            "[DEBUG] HH letter field keyboard resync: "
            f"value_matches={value_matches}"
        )
        return value_matches
    except Exception as exc:
        print(
            "[DEBUG] HH letter field keyboard resync failed: "
            f"{type(exc).__name__}"
        )
        return False


def _hh_submit_post_apply_letter(submit, *, fallback: bool = False) -> None:
    if not fallback:
        submit.click(timeout=5000)
        return

    mode = submit.evaluate(
        """
        el => {
          const form = el.form || el.closest('form');
          if (form && typeof form.requestSubmit === 'function') {
            form.requestSubmit(el);
            return 'requestSubmit';
          }
          el.click();
          return 'dom-click';
        }
        """
    )
    print(f"[DEBUG] HH post-apply fallback submit mode={mode}")


def _hh_attach_post_apply_cover_letter_strict(page, application):
    def incomplete(reason):
        message = (
            "HH подтвердил отклик, но сопроводительное письмо не подтверждено: "
            + reason
        )
        print("[MANUAL] " + message)
        hh_worker.set_status(
            application.id,
            "manual_required",
            applied=True,
            manual_reason=message,
        )
        return "manual_required"

    try:
        cover_letter = (application.cover_letter or "").strip()
        if not cover_letter:
            return incomplete("текст отсутствует.")

        field = None
        for _ in range(10):
            reason = hh_worker.detect_manual_required(page)
            if reason:
                return incomplete(reason)
            field = _hh_find_post_apply_cover_letter_field(page)
            if field is not None:
                break
            page.wait_for_timeout(500)

        if field is None:
            return incomplete("не найдено поле письма.")

        field.fill(cover_letter)
        try:
            field.press("Tab", timeout=2000)
            page.wait_for_timeout(200)
        except Exception:
            pass
        if field.input_value(timeout=2000).strip() != cover_letter:
            return incomplete("текст в поле не совпадает с подготовленным письмом.")

        submit = _hh_find_letter_submit_robust(field)
        if submit is None:
            return incomplete("не найдена кнопка прикрепления письма.")

        reason = hh_worker.detect_manual_required(page)
        if reason:
            return incomplete(reason)

        before_text = hh_worker.page_text(page)

        for attempt in (1, 2):
            print(f"[DEBUG] HH post-apply submit attempt={attempt}")
            try:
                _hh_submit_post_apply_letter(
                    submit,
                    fallback=(attempt == 2),
                )
            except hh_worker.PlaywrightTimeoutError:
                print(
                    "[WARN] Timeout прикрепления; проверяю результат "
                    "перед любым повтором."
                )

            for _ in range(12):
                if hh_worker.letter_delivery_confirmed(page, cover_letter, before_text):
                    print(
                        "[SUCCESS] Отклик и отдельное сопроводительное подтверждены HH."
                    )
                    hh_worker.set_status(application.id, "applied", applied=True)
                    return "applied"
                page.wait_for_timeout(500)

            reason = hh_worker.detect_manual_required(page)
            if reason:
                return incomplete(reason)

            if attempt == 2:
                break

            if not _hh_post_apply_form_still_unsent(field, submit, cover_letter):
                return incomplete("HH не показал подтверждение прикрепления.")

            if not _hh_resync_letter_field_for_retry(field, cover_letter):
                return incomplete(
                    "не удалось синхронизировать поле письма перед безопасным повтором."
                )

            refreshed_submit = _hh_find_letter_submit_robust(field)
            if refreshed_submit is None:
                return incomplete("форма письма изменилась после первого submit.")

            print(
                "[WARN] HH оставил неизменённую неотправленную форму письма; "
                "повторяю submit один раз через альтернативное событие."
            )
            submit = refreshed_submit

        return incomplete(
            "HH не показал подтверждение прикрепления после безопасного повтора."
        )
    except Exception as exc:
        return incomplete(f"ошибка прикрепления ({type(exc).__name__}).")


def _hh_safe_letter_submit(candidate) -> bool:
    try:
        if not candidate.is_visible() or not candidate.is_enabled():
            return False

        data_qa = (candidate.get_attribute("data-qa") or "").lower()
        text = (
            candidate.inner_text(timeout=1000)
            or candidate.get_attribute("value")
            or ""
        ).strip().lower()

        if "generate" in data_qa or "regenerate" in data_qa:
            return False
        if text.startswith(("сгенерировать", "перегенерировать")):
            return False

        letter_action_text = any(
            text == label.lower() or text.startswith(label.lower() + " ")
            for label in _HH_LETTER_SUBMIT_TEXTS
        )

        if "vacancy-response-link" in data_qa:
            return False
        if (
            "vacancy-response-submit" in data_qa
            and "letter" not in data_qa
            and "cover" not in data_qa
            and not letter_action_text
        ):
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


def _hh_selected_submit(candidate, reason: str):
    print(
        f"[DEBUG] HH post-apply letter submit selected ({reason}): "
        f"{_hh_control_metadata(candidate)}"
    )
    return candidate


def _hh_dump_letter_controls(field) -> None:
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
    form = field.locator("xpath=ancestor::form[1]")
    if _hh_locator_exists(form):
        for selector in _HH_LETTER_SPECIFIC_SUBMIT_SELECTORS:
            candidate = _hh_first_safe(form, selector)
            if candidate is not None:
                return _hh_selected_submit(candidate, f"form-specific:{selector}")

        for text in _HH_LETTER_SUBMIT_TEXTS:
            try:
                candidates = form.get_by_role("button", name=text, exact=False)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if _hh_safe_letter_submit(candidate):
                        return _hh_selected_submit(candidate, f"form-text:{text}")
            except Exception:
                continue

        try:
            submits = form.locator('button[type="submit"], input[type="submit"]')
            safe = []
            for index in range(submits.count()):
                candidate = submits.nth(index)
                if _hh_safe_letter_submit(candidate):
                    safe.append(candidate)
            if len(safe) == 1:
                return _hh_selected_submit(safe[0], "form-unique-submit")
        except Exception:
            pass

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

        for selector in _HH_LETTER_SPECIFIC_SUBMIT_SELECTORS:
            candidate = _hh_first_safe(scope, selector)
            if candidate is not None:
                return _hh_selected_submit(candidate, f"scope-specific:{selector}")

        for text in _HH_LETTER_SUBMIT_TEXTS:
            try:
                candidates = scope.get_by_role("button", name=text, exact=False)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if _hh_safe_letter_submit(candidate):
                        return _hh_selected_submit(candidate, f"scope-text:{text}")
            except Exception:
                continue

    _hh_dump_letter_controls(field)
    return None


def load_hh_queue():
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
    original_attach_post_apply_cover_letter = hh_worker.attach_post_apply_cover_letter
    hh_worker.load_queue = lambda: queue
    hh_worker.find_letter_submit = _hh_find_letter_submit_robust
    hh_worker.attach_post_apply_cover_letter = _hh_attach_post_apply_cover_letter_strict
    try:
        hh_worker.main()
    finally:
        hh_worker.load_queue = original_hh_load_queue
        hh_worker.find_letter_submit = original_find_letter_submit
        hh_worker.attach_post_apply_cover_letter = original_attach_post_apply_cover_letter


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