from __future__ import annotations

import html
import json as json_module
import os
import time
from collections.abc import Callable
from urllib.request import Request, urlopen

from hh_accounts import account_label

def _post_json(
    url: str,
    *,
    json: dict,
    timeout: float,
):
    request = Request(
        url,
        data=json_module.dumps(json).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urlopen(request, timeout=timeout)


def build_manual_required_message(
    *,
    vacancy_title: str,
    company: str | None,
    application_id: int,
    reason: str,
    application_sent: bool = False,
    cover_letter: str | None = None,
    account_key: str | None = None,
) -> str:
    safe_title = html.escape(vacancy_title or "Вакансия")
    safe_company = html.escape(company or "Компания не указана")
    safe_reason = html.escape(reason)

    next_step = (
        "Отклик уже отправлен. Открой вакансию и проверь или приложи сопроводительное письмо."
        if application_sent else
        "Автоматический отклик не считается отправленным. Открой вакансию и заверши его вручную."
    )
    return (
        f"{account_label(account_key)} · ⚠️ <b>Отклик требует внимания</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"{safe_company}\n\n"
        f"Причина: {safe_reason}\n"
        f"Application ID: <code>{application_id}</code>\n\n"
        f"{next_step}"
    )


def build_cover_letter_attention_message(
    *,
    vacancy_title: str,
    company: str | None,
    application_id: int,
    reason: str,
    cover_letter: str | None = None,
    account_key: str | None = None,
) -> str:
    safe_title = html.escape(vacancy_title or "Вакансия")
    safe_company = html.escape(company or "Компания не указана")
    safe_reason = html.escape(reason)
    return (
        f"{account_label(account_key)} · ✉️ <b>Отклик отправлен, письмо требует внимания</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"{safe_company}\n\n"
        f"HH уже подтвердил отправку резюме.\n"
        f"Сопроводительное пока не подтверждено: {safe_reason}\n"
        f"Application ID: <code>{application_id}</code>\n\n"
        "Повторно откликаться не нужно. Можно открыть вакансию и проверить письмо."
    )




def _send_telegram_payload(
    *,
    send: Callable,
    endpoint: str,
    payload: dict,
    attempts: int,
    retry_delay_seconds: float,
    sleep: Callable[[float], None],
    label: str,
    application_id: int,
) -> bool:
    total_attempts = max(1, attempts)

    for attempt in range(1, total_attempts + 1):
        try:
            response = send(
                endpoint,
                json=payload,
                timeout=15.0,
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if raise_for_status is not None:
                raise_for_status()
            close = getattr(response, "close", None)
            if close is not None:
                close()
            print(
                f"[TELEGRAM] {label} sent "
                f"for application_id={application_id}."
            )
            return True
        except Exception as exc:
            print(
                f"[TELEGRAM] {label} failed "
                f"(attempt {attempt}/{total_attempts}): "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < total_attempts:
                sleep(retry_delay_seconds)

    return False


def _cover_letter_payload(
    *,
    chat_id: str,
    cover_letter: str | None,
) -> dict | None:
    text = (cover_letter or "").strip()
    if not text:
        return None

    return {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

def notify_cover_letter_attention(
    *,
    vacancy_title: str,
    company: str | None,
    vacancy_url: str,
    application_id: int,
    reason: str,
    cover_letter: str | None = None,
    account_key: str | None = None,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    post: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print(
            "[TELEGRAM] cover-letter attention skipped: "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )
        return False

    send = post or _post_json
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    status_payload = {
        "chat_id": chat_id,
        "text": build_cover_letter_attention_message(
            vacancy_title=vacancy_title,
            company=company,
            application_id=application_id,
            reason=reason,
            cover_letter=cover_letter,
            account_key=account_key,
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "Проверить письмо",
                        "url": vacancy_url,
                    }
                ]
            ]
        },
    }

    if not _send_telegram_payload(
        send=send,
        endpoint=endpoint,
        payload=status_payload,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        label="cover-letter attention",
        application_id=application_id,
    ):
        return False

    letter_payload = _cover_letter_payload(
        chat_id=chat_id,
        cover_letter=cover_letter,
    )
    if letter_payload is None:
        return True

    return _send_telegram_payload(
        send=send,
        endpoint=endpoint,
        payload=letter_payload,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        label="cover-letter copy",
        application_id=application_id,
    )

def notify_manual_required(
    *,
    vacancy_title: str,
    company: str | None,
    vacancy_url: str,
    application_id: int,
    reason: str,
    application_sent: bool = False,
    cover_letter: str | None = None,
    account_key: str | None = None,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    post: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print(
            "[TELEGRAM] manual_required notification skipped: "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )
        return False

    send = post or _post_json
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    status_payload = {
        "chat_id": chat_id,
        "text": build_manual_required_message(
            vacancy_title=vacancy_title,
            company=company,
            application_id=application_id,
            reason=reason,
            application_sent=application_sent,
            cover_letter=cover_letter,
            account_key=account_key,
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "Проверить письмо" if application_sent else "Откликнуться вручную",
                        "url": vacancy_url,
                    }
                ]
            ]
        },
    }

    if not _send_telegram_payload(
        send=send,
        endpoint=endpoint,
        payload=status_payload,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        label="manual_required notification",
        application_id=application_id,
    ):
        return False

    letter_payload = _cover_letter_payload(
        chat_id=chat_id,
        cover_letter=cover_letter,
    )
    if letter_payload is None:
        return True

    return _send_telegram_payload(
        send=send,
        endpoint=endpoint,
        payload=letter_payload,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
        sleep=sleep,
        label="cover-letter copy",
        application_id=application_id,
    )
