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


def notify_captcha_pause(
    *,
    account_key: str,
    reason: str,
    application_id: int | None = None,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    post: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print(
            "[TELEGRAM] captcha pause notification skipped: "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )
        return False

    safe_reason = html.escape(reason or "HH запросил проверку")
    app_line = (
        f"\nApplication ID: <code>{application_id}</code>"
        if application_id is not None
        else ""
    )
    payload = {
        "chat_id": chat_id,
        "text": (
            f"{account_label(account_key)} · 🛑 <b>HH остановлен на CAPTCHA</b>\n\n"
            f"Причина: {safe_reason}{app_line}\n\n"
            "Новые действия по этому HH-аккаунту остановлены. "
            "Пройди CAPTCHA вручную в браузерном профиле аккаунта, "
            "затем отправь боту <code>/hh_resume "
            f"{html.escape(account_key)}</code>."
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    send = post or _post_json
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = send(endpoint, json=payload, timeout=15.0)
            raise_for_status = getattr(response, "raise_for_status", None)
            if raise_for_status is not None:
                raise_for_status()
            close = getattr(response, "close", None)
            if close is not None:
                close()
            print(
                "[TELEGRAM] captcha pause sent "
                f"for account={account_key} application_id={application_id}."
            )
            return True
        except Exception as exc:
            print(
                "[TELEGRAM] captcha pause failed "
                f"(attempt {attempt}/{max(1, attempts)}): "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < max(1, attempts):
                sleep(retry_delay_seconds)
    return False


def notify_cover_letter_attention(
    *,
    vacancy_title: str,
    company: str | None,
    vacancy_url: str,
    application_id: int,
    reason: str,
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
    payload = {
        "chat_id": chat_id,
        "text": build_cover_letter_attention_message(
            vacancy_title=vacancy_title,
            company=company,
            application_id=application_id,
            reason=reason,
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

    for attempt in range(1, max(1, attempts) + 1):
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
                "[TELEGRAM] cover-letter attention sent "
                f"for application_id={application_id}."
            )
            return True
        except Exception as exc:
            print(
                "[TELEGRAM] cover-letter attention failed "
                f"(attempt {attempt}/{max(1, attempts)}): "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < max(1, attempts):
                sleep(retry_delay_seconds)

    return False


def notify_manual_required(
    *,
    vacancy_title: str,
    company: str | None,
    vacancy_url: str,
    application_id: int,
    reason: str,
    application_sent: bool = False,
    account_key: str | None = None,
    cover_letter: str | None = None,
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

    def send_payload(payload: dict, label: str) -> bool:
        for attempt in range(1, max(1, attempts) + 1):
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
                    f"(attempt {attempt}/{max(1, attempts)}): "
                    f"{type(exc).__name__}: {exc}"
                )
                if attempt < max(1, attempts):
                    sleep(retry_delay_seconds)
        return False

    card_payload = {
        "chat_id": chat_id,
        "text": build_manual_required_message(
            vacancy_title=vacancy_title,
            company=company,
            application_id=application_id,
            reason=reason,
            application_sent=application_sent,
            account_key=account_key,
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": (
                            "\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u0438\u0441\u044c\u043c\u043e"
                            if application_sent
                            else "\u041e\u0442\u043a\u043b\u0438\u043a\u043d\u0443\u0442\u044c\u0441\u044f \u0432\u0440\u0443\u0447\u043d\u0443\u044e"
                        ),
                        "url": vacancy_url,
                    }
                ]
            ]
        },
    }
    if not send_payload(card_payload, "manual_required notification"):
        return False

    letter = (cover_letter or "").strip()
    if not letter:
        print(
            "[TELEGRAM] manual_required cover letter missing "
            f"for application_id={application_id}; card sent without second message."
        )
        return True

    letter_payload = {
        "chat_id": chat_id,
        "text": letter,
        "disable_web_page_preview": True,
    }
    return send_payload(
        letter_payload,
        "manual_required cover letter",
    )
