from __future__ import annotations

import html
import json as json_module
import os
import time
from collections.abc import Callable
from urllib.request import Request, urlopen

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
) -> str:
    safe_title = html.escape(vacancy_title or "Вакансия")
    safe_company = html.escape(company or "Компания не указана")
    safe_reason = html.escape(reason)

    return (
        "⚠️ <b>Отклик требует внимания</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"{safe_company}\n\n"
        f"Причина: {safe_reason}\n"
        f"Application ID: <code>{application_id}</code>\n\n"
        "Автоматический отклик не считается отправленным. "
        "Открой вакансию и заверши его вручную."
    )


def notify_manual_required(
    *,
    vacancy_title: str,
    company: str | None,
    vacancy_url: str,
    application_id: int,
    reason: str,
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
    payload = {
        "chat_id": chat_id,
        "text": build_manual_required_message(
            vacancy_title=vacancy_title,
            company=company,
            application_id=application_id,
            reason=reason,
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "Откликнуться вручную",
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
            print(
                "[TELEGRAM] manual_required notification sent "
                f"for application_id={application_id}."
            )
            return True
        except Exception as exc:
            print(
                "[TELEGRAM] manual_required notification failed "
                f"(attempt {attempt}/{max(1, attempts)}): "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < max(1, attempts):
                sleep(retry_delay_seconds)

    return False
