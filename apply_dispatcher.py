from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy import or_, select

import apply_worker as hh_worker
import ozon_apply_worker
import tbank_apply_worker
import vk_apply_worker
import yandex_apply_worker
from app.db import Application, SessionLocal, Vacancy
from hh_session_guard import check_hh_session


YANDEX_APPLY_LIVE = os.getenv("YANDEX_APPLY_LIVE", "false").lower() == "true"
YANDEX_APPLY_APPLICATION_ID = os.getenv("YANDEX_APPLY_APPLICATION_ID", "").strip()
VK_APPLY_LIVE = os.getenv("VK_APPLY_LIVE", "false").lower() == "true"
VK_APPLY_APPLICATION_ID = os.getenv("VK_APPLY_APPLICATION_ID", "").strip()
TBANK_APPLY_LIVE = os.getenv("TBANK_APPLY_LIVE", "false").lower() == "true"
TBANK_APPLY_APPLICATION_ID = os.getenv("TBANK_APPLY_APPLICATION_ID", "").strip()
OZON_ENABLED = os.getenv("OZON_ENABLED", "false").lower() == "true"
OZON_APPLY_LIVE = os.getenv("OZON_APPLY_LIVE", "false").lower() == "true"
OZON_APPLY_APPLICATION_ID = os.getenv("OZON_APPLY_APPLICATION_ID", "").strip()
DISPATCH_HH = os.getenv("APPLY_DISPATCH_HH", "true").lower() == "true"
DISPATCH_EXTERNAL = (
    os.getenv("APPLY_DISPATCH_EXTERNAL", "true").lower() == "true"
)
HH_MANUAL_RECOVERY_MAX_PER_RUN = int(
    os.getenv("HH_MANUAL_RECOVERY_MAX_PER_RUN", "10")
)
HH_MANUAL_RECOVERY_HOURS = int(
    os.getenv("HH_MANUAL_RECOVERY_HOURS", "6")
)
HH_MANUAL_RECOVERY_MAX_ATTEMPTS = max(
    0,
    int(os.getenv("HH_MANUAL_RECOVERY_MAX_ATTEMPTS", "2")),
)
HH_MANUAL_RECOVERY_RETRY_MINUTES = max(
    1,
    int(os.getenv("HH_MANUAL_RECOVERY_RETRY_MINUTES", "30")),
)


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
    "резюме доставлено",
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

_HH_POPUP_SUBMIT_SELECTORS = [
    'button[data-qa="vacancy-response-submit-popup"]',
    '[role="button"][data-qa="vacancy-response-submit-popup"]',
]

_HH_LETTER_SUBMIT_TEXTS = [
    "Приложить",
    "Добавить",
    "Отправить",
    "Сохранить",
    "Готово",
    "Подтвердить",
]

_HH_CHAT_TOPIC_SELECTORS = [
    '[data-qa="vacancy-response-link-view-topic"]',
    '[data-qa="vacancy-response-link-chat"]',
    '[data-qa="vacancy-chat-link"]',
    '[data-qa="vacancy-chat-button"]',
]

_HH_CHAT_COMPOSER_SELECTORS = [
    'textarea[data-qa="text-input"]',
    'textarea[data-qa="chatik-new-message-text"]',
]

_HH_CHAT_SEND_SELECTORS = [
    'button[data-qa="chatik-do-send-message"]',
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


def _hh_associated_form(page, submit):
    """Resolve the HTML form associated with a submit control.

    HH can place the post-apply submit button outside the <form> and link it
    via the HTML form="..." attribute.
    """
    try:
        form = submit.locator("xpath=ancestor::form[1]")
        if _hh_locator_exists(form):
            return form
    except Exception:
        pass

    try:
        form_id = (submit.get_attribute("form") or "").strip()
    except Exception:
        form_id = ""

    if not form_id:
        try:
            form_id = str(
                submit.evaluate("el => (el.form && el.form.id) || ''")
                or ""
            ).strip()
        except Exception:
            form_id = ""

    if not form_id:
        return None

    try:
        form = page.locator(f'form[id="{form_id}"]')
        if _hh_locator_exists(form):
            return form
    except Exception:
        return None

    return None


def _hh_submit_post_apply_letter(page, submit, *, fallback: bool = False) -> dict:
    if not fallback:
        submit.click(timeout=5000)
        return {
            "mode": "click",
            "confirmed": False,
        }

    form = _hh_associated_form(page, submit)
    if form is not None:
        try:
            meta = form.evaluate(
                """
                el => ({
                  id: el.getAttribute('id'),
                  action: el.action || el.getAttribute('action') || '',
                  method: (el.method || el.getAttribute('method') || 'get').toLowerCase()
                })
                """
            )
        except Exception:
            meta = {}

        action = str(meta.get("action") or "")
        method = str(meta.get("method") or "").lower()
        form_id = str(meta.get("id") or "")
        safe_edit_form = (
            "/applicant/vacancy_response/edit_ajax" in action
            or form_id.startswith("cover-letter-")
        )

        print(
            "[DEBUG] HH post-apply fallback form: "
            f"id={form_id!r} action={action!r} method={method!r} "
            f"safe_edit_form={safe_edit_form}"
        )

        if safe_edit_form:
            try:
                with page.expect_response(
                    lambda response: (
                        "/applicant/vacancy_response/edit_ajax" in response.url
                        and response.request.method.upper() == "POST"
                    ),
                    timeout=7000,
                ) as response_info:
                    form.evaluate(
                        """
                        el => HTMLFormElement.prototype.submit.call(el)
                        """
                    )

                response = response_info.value
                print(
                    "[DEBUG] HH native cover-letter form submit response: "
                    f"status={response.status} ok={response.ok} url={response.url}"
                )
                return {
                    "mode": "native-form-submit",
                    "confirmed": bool(response.ok),
                    "status": response.status,
                    "url": response.url,
                }
            except Exception as exc:
                print(
                    "[DEBUG] HH native cover-letter form submit failed: "
                    f"{type(exc).__name__}"
                )

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
    return {
        "mode": mode,
        "confirmed": False,
    }


def _hh_letter_probe(cover_letter: str) -> str:
    for line in (cover_letter or "").splitlines():
        normalized = " ".join(line.split())
        if len(normalized) >= 24 and not normalized.lower().startswith("здравствуйте"):
            return normalized[:80]
    return " ".join((cover_letter or "").split())[:80]


def _hh_first_visible_in_context(context, selectors):
    for selector in selectors:
        try:
            locator = context.locator(selector)
            count = locator.count()
        except Exception:
            continue

        if not isinstance(count, int):
            continue

        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue

    return None


def _hh_chat_context_and_composer(page):
    for _ in range(20):
        try:
            chat_frames = [
                frame
                for frame in page.frames
                if "chatik.hh.ru/chat" in (frame.url or "")
            ]
        except Exception:
            chat_frames = []

        if len(chat_frames) == 1:
            composer = _hh_first_visible_in_context(
                chat_frames[0],
                _HH_CHAT_COMPOSER_SELECTORS,
            )
            if composer is not None:
                return chat_frames[0], composer
        elif len(chat_frames) > 1:
            print(
                "[WARN] HH chat fallback: найдено несколько chatik-frame; "
                "не выбираю переписку наугад."
            )
            return None, None

        composer = _hh_first_visible_in_context(
            page,
            _HH_CHAT_COMPOSER_SELECTORS,
        )
        if composer is not None:
            return page, composer

        page.wait_for_timeout(250)

    return None, None


def _hh_response_card_snapshot(context, cover_letter: str) -> dict:
    """Inspect only the workflow card that represents the original HH response.

    Ordinary chat messages must never count as a cover letter.  The response
    card is the message whose data-qa is exactly chatik-chat-message-<digits>
    and whose text contains "Отклик на вакансию".
    """
    probe = " ".join(_hh_letter_probe(cover_letter).split())
    try:
        messages = context.locator('[data-qa^="chatik-chat-message-"]')
        count = messages.count()
    except Exception:
        return {"state": "unknown", "reason": "не удалось прочитать карточки чата"}

    for index in range(count):
        message = messages.nth(index)
        try:
            if not message.is_visible():
                continue
            data_qa = message.get_attribute("data-qa") or ""
            suffix = data_qa.removeprefix("chatik-chat-message-")
            if not suffix.isdigit():
                continue
            raw_text = message.inner_text(timeout=2500)
        except Exception:
            continue

        normalized = " ".join(raw_text.split())
        lowered = normalized.lower()
        if "отклик на вакансию" not in lowered:
            continue

        action = _hh_first_visible_in_context(
            message,
            ['[data-qa="chatik-chat-message-applicant-action"]'],
        )
        if action is not None:
            try:
                action_text = " ".join(
                    action.inner_text(timeout=1500).split()
                ).lower()
            except Exception:
                action_text = ""
            if "добавить сопроводительное" not in action_text:
                action = None

        missing_marker = "без сопроводительного письма" in lowered
        has_letter_probe = bool(probe and probe in normalized)

        if missing_marker and has_letter_probe:
            return {
                "state": "unknown",
                "reason": (
                    "карточка одновременно содержит письмо и маркер "
                    "«Без сопроводительного письма»"
                ),
                "message": message,
                "action": action,
                "text": normalized,
            }

        if missing_marker:
            return {
                "state": "missing",
                "reason": "карточка отклика явно показывает отсутствие письма",
                "message": message,
                "action": action,
                "text": normalized,
            }

        if has_letter_probe:
            return {
                "state": "confirmed",
                "reason": "письмо находится внутри карточки отклика",
                "message": message,
                "action": action,
                "text": normalized,
            }

        return {
            "state": "unknown",
            "reason": "карточка отклика найдена, но состояние письма не распознано",
            "message": message,
            "action": action,
            "text": normalized,
        }

    return {
        "state": "unknown",
        "reason": "карточка «Отклик на вакансию» не найдена",
    }


def _hh_open_response_chat(page):
    current_url = page.url
    if "/vacancy/" not in current_url:
        return None, None, "текущая страница не является вакансией"

    try:
        page.goto(
            current_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(1000)
    except Exception as exc:
        return None, None, (
            "не удалось переоткрыть вакансию "
            f"({type(exc).__name__})"
        )

    if not hh_worker.already_applied(page):
        return None, None, (
            "HH после перезагрузки не подтверждает существующий отклик"
        )

    topic = None
    for _ in range(20):
        topic = _hh_first_visible_in_context(
            page,
            _HH_CHAT_TOPIC_SELECTORS,
        )
        if topic is not None:
            break
        page.wait_for_timeout(250)

    if topic is None:
        return None, None, "у отклика нет доступной ссылки на чат"

    try:
        topic.click(timeout=5000)
    except Exception as exc:
        return None, None, f"не удалось открыть чат ({type(exc).__name__})"

    context, composer = _hh_chat_context_and_composer(page)
    if context is None or composer is None:
        return None, None, "не загрузился Chatik/composer отклика"

    return context, composer, None


def _hh_wait_response_card_state(context, cover_letter: str) -> dict:
    snapshot = {"state": "unknown", "reason": "карточка ещё не загружена"}
    for _ in range(24):
        snapshot = _hh_response_card_snapshot(context, cover_letter)
        if snapshot.get("state") in {"confirmed", "missing"}:
            return snapshot
        try:
            context.page.wait_for_timeout(250)
        except Exception:
            break
    return snapshot


def _hh_cover_mode_active(context) -> bool:
    """True only for Chatik's native applicant cover-letter edit preview."""
    preview = _hh_first_visible_in_context(
        context,
        ['[data-qa="chat-input-preview"]'],
    )
    if preview is None:
        return False

    try:
        text = " ".join(preview.inner_text(timeout=2500).split()).lower()
    except Exception:
        return False

    return (
        "сопроводительное письмо" in text
        and "введите текст сопроводительного письма" in text
    )

def _hh_activate_cover_mode(page, context, action) -> bool:
    for attempt in (1, 2):
        try:
            action.click(timeout=5000)
        except Exception:
            if attempt == 2:
                return False

        for _ in range(20):
            if _hh_cover_mode_active(context):
                return True
            page.wait_for_timeout(250)

        if attempt == 1:
            refreshed = _hh_first_visible_in_context(
                context,
                ['[data-qa="chatik-chat-message-applicant-action"]'],
            )
            if refreshed is None:
                return False
            action = refreshed

    return False


def _hh_verify_response_card(page, cover_letter: str) -> tuple[dict, object, object]:
    context, composer, error = _hh_open_response_chat(page)
    if error:
        return {
            "state": "unknown",
            "reason": error,
        }, None, None

    snapshot = _hh_wait_response_card_state(context, cover_letter)
    return snapshot, context, composer


_HH_CHAT_COVER_SAVE_ENDPOINT = "/chatik/api/save"


def _hh_response_card_message_id(snapshot) -> int | None:
    message = snapshot.get("message")
    if message is None:
        return None
    try:
        data_qa = message.get_attribute("data-qa") or ""
        suffix = data_qa.removeprefix("chatik-chat-message-")
        return int(suffix) if suffix.isdigit() else None
    except Exception:
        return None


def _hh_attach_cover_letter_via_response_card(
    page,
    cover_letter: str,
) -> tuple[bool, str]:
    """Use HH's native Chatik cover-letter edit mode.

    HH reuses the normal Chatik textarea and send arrow while an applicant-only
    chat-input-preview is active. In that state Chatik does not send a normal
    message: it POSTs /chatik/api/save with the original response-card
    messageId. We require both the special preview and that exact network
    mutation before accepting the result.
    """
    snapshot, context, composer = _hh_verify_response_card(
        page,
        cover_letter,
    )
    state = snapshot.get("state")
    if state == "confirmed":
        return True, snapshot.get("reason") or "cover letter already attached"

    if state != "missing":
        return False, snapshot.get("reason") or "cover-letter state is unknown"

    action = snapshot.get("action")
    if action is None:
        return False, (
            "response card says letter is missing but native "
            "«Добавить сопроводительное» action is unavailable"
        )

    message_id = _hh_response_card_message_id(snapshot)
    if message_id is None:
        return False, "could not determine original response-card messageId"

    if composer is None:
        return False, "Chatik composer is unavailable"

    try:
        draft_before = composer.input_value(timeout=2000).strip()
    except Exception:
        draft_before = ""
    if draft_before:
        return False, "Chatik composer already contains another draft; not overwriting it"

    if not _hh_activate_cover_mode(page, context, action):
        return False, "native applicant action did not activate cover-letter mode"

    if not _hh_cover_mode_active(context):
        return False, "native cover-letter preview is not visibly active"

    try:
        composer.fill(cover_letter)
        typed = composer.input_value(timeout=3000).strip()
    except Exception as exc:
        return False, f"failed to fill Chatik cover-letter composer ({type(exc).__name__})"

    if typed != cover_letter:
        return False, "Chatik cover-letter composer value does not match prepared letter"

    if not _hh_cover_mode_active(context):
        return False, "cover-letter preview disappeared before submit"

    send = _hh_first_visible_in_context(context, _HH_CHAT_SEND_SELECTORS)
    if send is None:
        return False, "Chatik submit arrow is unavailable in cover-letter mode"
    try:
        if not send.is_enabled():
            return False, "Chatik submit arrow is disabled in cover-letter mode"
    except Exception:
        pass

    save_requests = []
    save_responses = []

    def capture_request(request):
        try:
            if (
                request.method == "POST"
                and _HH_CHAT_COVER_SAVE_ENDPOINT in request.url
            ):
                try:
                    payload = request.post_data_json
                except Exception:
                    payload = None
                save_requests.append((request.url, payload))
        except Exception:
            pass

    def capture_response(response):
        try:
            if (
                response.request.method == "POST"
                and _HH_CHAT_COVER_SAVE_ENDPOINT in response.url
            ):
                save_responses.append((response.status, response.url))
        except Exception:
            pass

    page.on("request", capture_request)
    page.on("response", capture_response)
    try:
        send.click(timeout=5000)
        for _ in range(20):
            if save_requests and save_responses:
                break
            page.wait_for_timeout(250)
    except Exception as exc:
        return False, f"failed to submit Chatik cover letter ({type(exc).__name__})"
    finally:
        try:
            page.remove_listener("request", capture_request)
        except Exception:
            pass
        try:
            page.remove_listener("response", capture_response)
        except Exception:
            pass

    valid_request = False
    request_summary = []
    for url, payload in save_requests:
        if isinstance(payload, dict):
            payload_message_id = payload.get("messageId")
            payload_text = payload.get("text")
            request_summary.append(
                {
                    "messageId": payload_message_id,
                    "textLength": len(payload_text or ""),
                }
            )
            if (
                str(payload_message_id) == str(message_id)
                and payload_text == cover_letter
            ):
                valid_request = True
        else:
            request_summary.append({"payload": "unparsed"})

    if not valid_request:
        return False, (
            "Chatik did not emit the expected native /save payload "
            f"for response message {message_id}: {request_summary[-3:]}"
        )

    successful_save = any(
        status < 400
        for status, _ in save_responses
    )
    if not successful_save:
        return False, (
            "Chatik native /save did not return success: "
            f"{save_responses[-3:]}"
        )

    print(
        "[DEBUG] HH Chatik cover-letter native save: "
        f"messageId={message_id}, responses={save_responses[-3:]}"
    )

    verified, _, _ = _hh_verify_response_card(
        page,
        cover_letter,
    )
    if verified.get("state") == "confirmed":
        return True, (
            "cover letter saved through Chatik edit mode and confirmed "
            "inside original response card"
        )

    return False, (
        "native Chatik /save succeeded but response card did not confirm "
        "cover letter: "
        + (verified.get("reason") or "unknown state")
    )

def _hh_attach_post_apply_cover_letter_strict(page, application):
    cover_letter = (application.cover_letter or "").strip()

    def confirmed(reason: str):
        print(f"[SUCCESS] HH cover letter confirmed: {reason}")
        hh_worker.set_cover_letter_status(
            application.id,
            "confirmed",
        )
        hh_worker.set_status(
            application.id,
            "applied",
            applied=True,
        )
        return "applied"

    def incomplete(reason: str):
        print(
            "[LETTER ATTENTION] HH подтвердил отклик, но письмо "
            f"не подтверждено: {reason}"
        )
        hh_worker.set_status(
            application.id,
            "applied",
            applied=True,
        )
        hh_worker.set_cover_letter_status(
            application.id,
            "needs_manual",
            error=reason,
            notify=True,
        )
        return "applied"

    try:
        captcha_reason = hh_worker.captcha_reason_from_page(page)
        if captcha_reason:
            return hh_worker.pause_application_for_captcha(
                application,
                captcha_reason,
                application_already_sent=True,
            )

        if not cover_letter:
            hh_worker.set_status(
                application.id,
                "applied",
                applied=True,
            )
            hh_worker.set_cover_letter_status(
                application.id,
                "not_required",
            )
            return "applied"

        # If the response card already contains the prepared letter, never touch
        # any submit control again.  This is the final source of truth.
        snapshot, _, _ = _hh_verify_response_card(
            page,
            cover_letter,
        )
        captcha_reason = hh_worker.captcha_reason_from_page(page)
        if captcha_reason:
            return hh_worker.pause_application_for_captcha(
                application,
                captcha_reason,
                application_already_sent=True,
            )
        if snapshot.get("state") == "confirmed":
            return confirmed(snapshot.get("reason") or "response card")

        # Return to the vacancy and try the native post-apply popup once.
        current_url = page.url
        if "/vacancy/" not in current_url:
            return incomplete("не удалось определить URL вакансии")

        page.goto(
            current_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(900)

        field = None
        for _ in range(10):
            field = _hh_find_post_apply_cover_letter_field(page)
            if field is not None:
                break
            page.wait_for_timeout(300)

        if field is not None:
            field.fill(cover_letter)
            try:
                field.press("Tab", timeout=2000)
                page.wait_for_timeout(150)
            except Exception:
                pass

            if field.input_value(timeout=2000).strip() != cover_letter:
                return incomplete(
                    "текст в native post-apply поле не совпадает с письмом"
                )

            submit = _hh_find_letter_submit_robust(field)
            if submit is not None:
                print("[DEBUG] HH native post-apply submit: single UI click")
                try:
                    _hh_submit_post_apply_letter(
                        page,
                        submit,
                        fallback=False,
                    )
                except hh_worker.PlaywrightTimeoutError:
                    print(
                        "[WARN] Native cover-letter click timeout; "
                        "проверяю карточку отклика вместо повторного submit."
                    )
                except Exception as exc:
                    print(
                        "[WARN] Native cover-letter click failed: "
                        f"{type(exc).__name__}; проверяю response card."
                    )
                page.wait_for_timeout(900)
            else:
                print(
                    "[WARN] Native post-apply поле найдено, "
                    "но submit не найден; перехожу к response-card flow."
                )
        else:
            print(
                "[WARN] Native post-apply поле не найдено; "
                "перехожу к response-card flow."
            )

        # Never infer delivery from a stale popup or a second HTTP response.
        # Verify the original response card first.
        snapshot, _, _ = _hh_verify_response_card(
            page,
            cover_letter,
        )
        captcha_reason = hh_worker.captcha_reason_from_page(page)
        if captcha_reason:
            return hh_worker.pause_application_for_captcha(
                application,
                captcha_reason,
                application_already_sent=True,
            )
        if snapshot.get("state") == "confirmed":
            return confirmed(snapshot.get("reason") or "response card")

        # If the card explicitly says there is no letter, use HH's second
        # official post-apply scenario: +Добавить сопроводительное in Chatik.
        if snapshot.get("state") == "missing":
            delivered, reason = _hh_attach_cover_letter_via_response_card(
                page,
                cover_letter,
            )
            if delivered:
                return confirmed(reason)
            captcha_reason = hh_worker.captcha_reason_from_page(page)
            if captcha_reason:
                return hh_worker.pause_application_for_captcha(
                    application,
                    captcha_reason,
                    application_already_sent=True,
                )
            return incomplete(reason)

        return incomplete(
            snapshot.get("reason")
            or "HH не подтвердил состояние сопроводительного письма"
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
    field_meta = _hh_control_metadata(field)
    field_data_qa = (field_meta.get("dataQa") or "").lower()
    popup_field = "vacancy-response-popup-form-letter-input" in field_data_qa

    form = field.locator("xpath=ancestor::form[1]")
    if _hh_locator_exists(form):
        # Application 1648: the popup textarea was paired with both
        # vacancy-response-letter-submit and vacancy-response-submit-popup.
        # The latter is the actual popup form submit used by current HH.
        if popup_field:
            for selector in _HH_POPUP_SUBMIT_SELECTORS:
                candidate = _hh_first_safe(form, selector)
                if candidate is not None:
                    return _hh_selected_submit(candidate, f"popup-form:{selector}")

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

        if popup_field:
            for selector in _HH_POPUP_SUBMIT_SELECTORS:
                candidate = _hh_first_safe(scope, selector)
                if candidate is not None:
                    return _hh_selected_submit(candidate, f"popup-scope:{selector}")

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
        base = (
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Application.account_key == hh_worker.ACTIVE_ACCOUNT.key,
                or_(Vacancy.source == "hh", Vacancy.source.is_(None)),
            )
        )

        if hh_worker.ACTIVE_ACCOUNT.key != "old":
            rows = session.execute(
                base
                .order_by(Application.created_at.asc())
                .limit(hh_worker.MAX_PER_RUN)
            ).all()
        else:
            # Drain the historical OLD backlog while reserving one slot per
            # scheduler run for the newest discovery.
            newest_budget = 1 if hh_worker.MAX_PER_RUN > 1 else hh_worker.MAX_PER_RUN
            oldest_budget = max(0, hh_worker.MAX_PER_RUN - newest_budget)

            oldest = session.execute(
                base
                .order_by(Application.created_at.asc(), Application.id.asc())
                .limit(oldest_budget)
            ).all()
            selected_ids = [application.id for application, _ in oldest]

            newest_query = base.order_by(
                Vacancy.found_at.desc(),
                Application.id.desc(),
            ).limit(newest_budget)
            if selected_ids:
                newest_query = newest_query.where(
                    ~Application.id.in_(selected_ids)
                )
            newest = session.execute(newest_query).all()
            rows = oldest + newest

        result = []
        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))
        return result
    finally:
        session.close()


def load_hh_cover_letter_recovery_queue():
    """Retry only letters for HH responses already confirmed as submitted.

    Recovery never presses the vacancy's primary apply button. The response
    transport status stays applied while letter delivery is tracked
    independently in cover_letter_status.
    """
    session = SessionLocal()
    cutoff = datetime.utcnow() - timedelta(
        hours=HH_MANUAL_RECOVERY_HOURS
    )
    retry_cutoff = datetime.utcnow() - timedelta(
        minutes=HH_MANUAL_RECOVERY_RETRY_MINUTES
    )

    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "applied",
                Application.cover_letter_status == "needs_manual",
                Application.account_key == hh_worker.ACTIVE_ACCOUNT.key,
                Application.cover_letter.is_not(None),
                Application.created_at >= cutoff,
                Application.manual_recovery_attempts
                < HH_MANUAL_RECOVERY_MAX_ATTEMPTS,
                or_(
                    Application.manual_recovery_last_at.is_(None),
                    Application.manual_recovery_last_at <= retry_cutoff,
                ),
                or_(Vacancy.source == "hh", Vacancy.source.is_(None)),
            )
            .order_by(Application.created_at.desc())
            .limit(HH_MANUAL_RECOVERY_MAX_PER_RUN)
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


def load_tbank_queue_approved():
    return _load_approved_queue(
        source="tbank",
        target_application_id=TBANK_APPLY_APPLICATION_ID,
        max_per_run=tbank_apply_worker.MAX_PER_RUN,
    )


def load_ozon_queue_approved():
    return _load_approved_queue(
        source="ozon",
        target_application_id=OZON_APPLY_APPLICATION_ID,
        max_per_run=ozon_apply_worker.MAX_PER_RUN,
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


def _mark_hh_manual_recovery_attempt(application_id: int) -> int:
    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return 0
        application.manual_recovery_attempts = int(
            application.manual_recovery_attempts or 0
        ) + 1
        application.manual_recovery_last_at = datetime.utcnow()
        attempts = application.manual_recovery_attempts
        session.commit()
        return attempts
    finally:
        session.close()


def _recover_hh_cover_letter_application(
    page,
    vacancy,
    application,
) -> str:
    """Repair only the letter for a response HH confirms already exists."""
    attempts = _mark_hh_manual_recovery_attempt(application.id)
    print()
    print("=" * 80)
    print(
        f"[LETTER RECOVERY] application_id={application.id} "
        f"attempt={attempts}/{HH_MANUAL_RECOVERY_MAX_ATTEMPTS} | "
        f"{vacancy.title} | {vacancy.company or '-'}"
    )
    print(vacancy.url)

    try:
        page.goto(
            vacancy.url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(1800)
        captcha_reason = hh_worker.captcha_reason_from_page(page)
        if captcha_reason:
            return hh_worker.pause_application_for_captcha(
                application,
                captcha_reason,
                application_already_sent=True,
            )
    except Exception as exc:
        reason = (
            "не удалось открыть вакансию "
            f"({type(exc).__name__}: {exc})"
        )
        print("[LETTER RECOVERY] " + reason)
        hh_worker.set_cover_letter_status(
            application.id,
            "needs_manual",
            error=reason,
        )
        return "applied"

    if not hh_worker.already_applied(page):
        reason = (
            "HH после перезагрузки не подтверждает существующий отклик; "
            "повторный submit запрещён"
        )
        print("[LETTER RECOVERY] " + reason)
        hh_worker.set_cover_letter_status(
            application.id,
            "needs_manual",
            error=reason,
        )
        return "applied"

    cover_letter = (application.cover_letter or "").strip()
    if not cover_letter:
        print(
            "[LETTER RECOVERY] Отклик существует, но подготовленного "
            "письма нет; письмо не требуется."
        )
        hh_worker.set_cover_letter_status(
            application.id,
            "not_required",
        )
        return "applied"

    print(
        "[LETTER RECOVERY] HH подтверждает существующий отклик. "
        "Пробую только штатную post-apply форму сопроводительного."
    )
    return _hh_attach_post_apply_cover_letter_strict(
        page,
        application,
    )


def _run_hh_source() -> None:
    if not DISPATCH_HH:
        print("HH dispatcher отключён через APPLY_DISPATCH_HH=false")
        return

    recovery_queue = load_hh_cover_letter_recovery_queue()
    queue = load_hh_queue()
    print("\n" + "=" * 80)
    print("Переход к HH queue")
    print("=" * 80)
    print(f"HH cover-letter recovery: {len(recovery_queue)}")
    print(f"HH approved в очереди: {len(queue)}")

    if not recovery_queue and not queue:
        print("HH: отправлять и восстанавливать нечего.")
        return

    session_status = check_hh_session(
        account=hh_worker.ACTIVE_ACCOUNT,
        headless=hh_worker.HEADLESS,
    )
    if (
        not session_status.authenticated
        or not session_status.identity_verified
    ):
        print(
            "[HH AUTH] HH-отклики остановлены: "
            f"{session_status.reason}"
        )
        if session_status.final_url:
            print(f"[HH AUTH] Final URL: {session_status.final_url}")
        print(
            "[HH AUTH] Approved-очередь оставлена без изменений. "
            "Recovery-очередь тоже оставлена без изменений. "
            "После повторного входа следующий Apply run попробует снова."
        )
        return

    original_hh_load_queue = hh_worker.load_queue
    original_hh_process_application = hh_worker.process_application
    original_find_letter_submit = hh_worker.find_letter_submit
    original_attach_post_apply_cover_letter = hh_worker.attach_post_apply_cover_letter

    hh_worker.find_letter_submit = _hh_find_letter_submit_robust
    hh_worker.attach_post_apply_cover_letter = _hh_attach_post_apply_cover_letter_strict

    try:
        if recovery_queue:
            print(
                "[HH LETTER RECOVERY] Проверяю письма только для уже "
                "подтверждённых откликов; повторный submit вакансии запрещён."
            )
            hh_worker.load_queue = lambda: recovery_queue
            hh_worker.process_application = _recover_hh_cover_letter_application
            hh_worker.main()

        if queue:
            hh_worker.load_queue = lambda: queue
            hh_worker.process_application = original_hh_process_application
            hh_worker.main()
    finally:
        hh_worker.load_queue = original_hh_load_queue
        hh_worker.process_application = original_hh_process_application
        hh_worker.find_letter_submit = original_find_letter_submit
        hh_worker.attach_post_apply_cover_letter = original_attach_post_apply_cover_letter

def main() -> None:
    print("=" * 80)
    print("APPLICATION DISPATCHER")
    print("HH -> apply_worker.py (source=hh, status=approved)")
    print("Yandex -> yandex_apply_worker.py (source=yandex, status=approved)")
    print("VK -> vk_apply_worker.py (source=vk, status=approved)")
    print("T-Bank -> tbank_apply_worker.py (source=tbank, status=approved)")
    print("Ozon -> ozon_apply_worker.py (source=ozon, status=approved; manual fallback while anti-bot blocks production)")
    print("=" * 80)

    _run_hh_source()

    if DISPATCH_EXTERNAL:
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

        _run_external_source(
            label="T-Bank",
            live=TBANK_APPLY_LIVE,
            target_application_id=TBANK_APPLY_APPLICATION_ID,
            queue=load_tbank_queue_approved(),
            worker=tbank_apply_worker,
        )

        if OZON_ENABLED:
            _run_external_source(
                label="Ozon",
                live=OZON_APPLY_LIVE,
                target_application_id=OZON_APPLY_APPLICATION_ID,
                queue=load_ozon_queue_approved(),
                worker=ozon_apply_worker,
            )
        else:
            print("[OZON] first-party dispatch disabled by OZON_ENABLED=false")
    else:
        print(
            "[MULTI ACCOUNT] External-source dispatch skipped in this "
            "account worker."
        )


if __name__ == "__main__":
    main()