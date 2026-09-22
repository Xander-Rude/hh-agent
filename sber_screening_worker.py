from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.sber_screening import generate_suggestion
from app.sber_screening_store import DEFAULT_STORE_PATH, SberScreeningStore


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GIGA_USERNAME = os.getenv("HH_SBER_GIGA_USERNAME", "Giga_recruiter_bot").lstrip("@")
SESSION_PATH = Path(
    os.getenv(
        "HH_SBER_TELEGRAM_SESSION",
        str(ROOT / "data" / "secrets" / "sber_user"),
    )
)
POLL_SECONDS = float(os.getenv("HH_SBER_APPROVAL_POLL_SECONDS", "2"))


def _enabled() -> bool:
    return os.getenv("HH_SBER_SCREENING_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _credentials() -> tuple[int, str, str, int]:
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("TELEGRAM_API_ID is missing or invalid") from exc

    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id_raw = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not api_hash:
        raise RuntimeError("TELEGRAM_API_HASH is missing")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not chat_id_raw:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    return api_id, api_hash, bot_token, int(chat_id_raw)


async def _notify(
    *,
    bot_token: str,
    chat_id: int,
    text: str,
    keyboard: dict | None = None,
) -> None:
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


def _flatten_buttons(message) -> list[str]:
    result: list[str] = []
    for row in getattr(message, "buttons", None) or []:
        for button in row:
            text = str(getattr(button, "text", "") or "").strip()
            if text:
                result.append(text)
    return result


def _pending_keyboard(turn_id: int, has_suggestion: bool) -> dict:
    row = []
    if has_suggestion:
        row.append(
            {
                "text": "✅ Отправить",
                "callback_data": f"sber_send:{turn_id}",
            }
        )
    row.append(
        {
            "text": "⏭ Не отправлять",
            "callback_data": f"sber_skip:{turn_id}",
        }
    )
    return {"inline_keyboard": [row]}


def _choice_keyboard(turn_id: int, options: list[str]) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": label[:55],
                    "callback_data": f"sber_choice:{turn_id}:{index}",
                }
            ]
            for index, label in enumerate(options)
        ]
        + [
            [
                {
                    "text": "⏭ Не отправлять",
                    "callback_data": f"sber_skip:{turn_id}",
                }
            ]
        ]
    }


async def _approval_loop(client, store: SberScreeningStore) -> None:
    while True:
        for turn in store.approved_turns():
            turn_id = int(turn["id"])
            payload = str(turn.get("approved_payload") or "")
            try:
                if payload.startswith("text:"):
                    answer = payload[5:].strip()
                    if not answer:
                        raise RuntimeError("approved text is empty")
                    await client.send_message(GIGA_USERNAME, answer)
                elif payload.startswith("button:"):
                    index = int(payload.split(":", 1)[1])
                    source = await client.get_messages(
                        GIGA_USERNAME,
                        ids=int(turn["external_message_id"]),
                    )
                    if source is None:
                        raise RuntimeError("source GigaRecruiter message not found")
                    await source.click(index)
                else:
                    raise RuntimeError("unknown approved payload")
                store.mark_sent(turn_id)
                print(f"[SBER SCREENING] sent turn #{turn_id}", flush=True)
            except Exception as exc:
                store.mark_error(turn_id, f"{type(exc).__name__}: {exc}")
                print(
                    f"[SBER SCREENING] send failed turn #{turn_id}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
        await asyncio.sleep(POLL_SECONDS)


async def run() -> None:
    if not _enabled():
        print(
            "[SBER SCREENING] disabled. Set HH_SBER_SCREENING_ENABLED=true after setup.",
            flush=True,
        )
        return

    try:
        from telethon import TelegramClient, events
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is not installed. Run setup_sber_screening.ps1."
        ) from exc

    api_id, api_hash, bot_token, chat_id = _credentials()
    store = SberScreeningStore(DEFAULT_STORE_PATH)
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telegram user session is not authorized. "
            "Run tools/sber_telegram_login.py interactively."
        )

    @client.on(events.NewMessage)
    async def on_message(event) -> None:
        sender = await event.get_sender()
        username = str(getattr(sender, "username", "") or "")
        if username.lower() != GIGA_USERNAME.lower():
            return

        session = store.get_active_session()
        if session is None:
            await _notify(
                bot_token=bot_token,
                chat_id=chat_id,
                text=(
                    "⚠️ ГигаРекрутер прислал сообщение, но Sber screening "
                    "не привязан к отклику. Сначала выполни /sber_arm "
                    "в HH Agent, затем повтори/продолжи диалог."
                ),
            )
            return

        session_id = int(session["id"])
        message_id = int(event.message.id)
        question = (event.raw_text or "").strip() or "(сообщение без текста)"
        options = _flatten_buttons(event.message)

        existing = store.create_turn(
            session_id=session_id,
            external_message_id=message_id,
            question=question,
            options=options,
        )
        if existing.get("status") != "pending" or existing.get("suggested_answer"):
            return

        store.set_session_active(session_id)

        if options:
            turn = existing
            await _notify(
                bot_token=bot_token,
                chat_id=chat_id,
                text=(
                    f"🟢 Сбер / ГигаРекрутер\n"
                    f"Application #{session['application_id']}\n\n"
                    f"{question[:2500]}\n\n"
                    "Выбери вариант. Ничего не уйдёт без твоего нажатия."
                ),
                keyboard=_choice_keyboard(int(turn["id"]), options),
            )
            return

        history = store.history(session_id)
        try:
            suggestion = await asyncio.to_thread(
                generate_suggestion,
                application_id=int(session["application_id"]),
                question=question,
                history=history,
            )
        except Exception as exc:
            suggestion = None
            reason = f"{type(exc).__name__}: {exc}"
            print(f"[SBER SCREENING] LLM failed: {reason}", flush=True)

        if suggestion is None:
            answer = ""
            confidence = "needs_user"
            reason_text = reason
        else:
            answer = suggestion.answer
            confidence = suggestion.confidence
            reason_text = suggestion.reason

        # create_turn is idempotent, so update the pending row with generated data.
        store.update_suggestion(
            int(existing["id"]),
            suggested_answer=answer or None,
            confidence=confidence,
            reason=reason_text,
        )

        turn = store.get_turn(int(existing["id"]))
        if answer:
            text = (
                f"🟢 Сбер / ГигаРекрутер\n"
                f"Application #{session['application_id']}\n\n"
                f"Вопрос:\n{question[:1800]}\n\n"
                f"Предлагаю ответ:\n{answer[:1500]}\n\n"
                f"Уверенность: {confidence}\n"
                f"Основание: {reason_text[:500]}\n\n"
                f"Для своего текста: /sber_answer {turn['id']} | текст"
            )
        else:
            text = (
                f"🟡 Сбер / ГигаРекрутер\n"
                f"Application #{session['application_id']}\n\n"
                f"Вопрос:\n{question[:2200]}\n\n"
                "Не могу честно ответить только из подтвержденных данных.\n"
                f"Нужно уточнить: {reason_text[:700]}\n\n"
                f"Ответ вручную: /sber_answer {turn['id']} | текст"
            )

        await _notify(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            keyboard=_pending_keyboard(int(turn["id"]), bool(answer)),
        )

    approval_task = asyncio.create_task(_approval_loop(client, store))
    print(
        f"[SBER SCREENING] listening @{GIGA_USERNAME}; "
        "copilot mode, auto-send disabled",
        flush=True,
    )
    try:
        await client.run_until_disconnected()
    finally:
        approval_task.cancel()
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
