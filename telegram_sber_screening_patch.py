from __future__ import annotations

from sqlalchemy import select

from app.db import Application, SessionLocal, Vacancy
from app.sber_screening_store import SberScreeningStore


_OWNER_CHAT_ID: int | None = None


def _authorized(update) -> bool:
    if _OWNER_CHAT_ID is None or update.effective_chat is None:
        return False
    return int(update.effective_chat.id) == int(_OWNER_CHAT_ID)


async def _deny(update) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer("Недоступно.", show_alert=True)
    elif update.message is not None:
        await update.message.reply_text("Недоступно.")


def _payload(update) -> str:
    text = (update.message.text or "").strip()
    return text.split(maxsplit=1)[1].strip() if " " in text else ""


def _resolve_application(raw: str) -> tuple[Application, Vacancy] | None:
    with SessionLocal() as session:
        if raw and raw.lower() not in {"latest", "последний"}:
            try:
                application_id = int(raw)
            except ValueError:
                return None
            application = session.get(Application, application_id)
            if application is None:
                return None
            vacancy = session.get(Vacancy, application.vacancy_id)
            if vacancy is None:
                return None
            session.expunge(application)
            session.expunge(vacancy)
            return application, vacancy

        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(Application.applied_at.is_not(None))
            .order_by(Application.id.desc())
        ).all()
        for application, vacancy in rows:
            haystack = f"{vacancy.company or ''} {vacancy.title or ''}".lower()
            if "сбер" in haystack or "sber" in haystack:
                session.expunge(application)
                session.expunge(vacancy)
                return application, vacancy
    return None


async def sber_arm_command(update, context) -> None:
    if not _authorized(update):
        await _deny(update)
        return

    raw = _payload(update)
    resolved = _resolve_application(raw)
    if resolved is None:
        await update.message.reply_text(
            "Не нашёл отклик Сбера. Формат: /sber_arm APPLICATION_ID\n"
            "Без ID команда берёт последний подтверждённый отклик Сбера."
        )
        return

    application, vacancy = resolved
    haystack = f"{vacancy.company or ''} {vacancy.title or ''}".lower()
    if "сбер" not in haystack and "sber" not in haystack:
        await update.message.reply_text(
            f"Application #{application.id} не похож на отклик Сбера. "
            "Ничего не активировано."
        )
        return

    store = SberScreeningStore()
    screening = store.arm(application.id)
    await update.message.reply_text(
        "🟢 Sber screening armed\n\n"
        f"Application #{application.id}\n"
        f"{vacancy.title}\n"
        f"{vacancy.company or 'Сбер'}\n\n"
        "Теперь открой уникальную ссылку ГигаРекрутера и нажми Start. "
        "Ответы будут приходить сюда на подтверждение. "
        "Без твоего нажатия агент ничего ГигаРекрутеру не отправит.\n\n"
        f"Screening session #{screening['id']}"
    )


async def sber_status_command(update, context) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    store = SberScreeningStore()
    session = store.get_active_session()
    if session is None:
        await update.message.reply_text(
            "Sber screening не активирован. Используй /sber_arm."
        )
        return
    pending = store.pending_turns(int(session["id"]))
    await update.message.reply_text(
        "Sber screening:\n"
        f"session #{session['id']}\n"
        f"application #{session['application_id']}\n"
        f"status: {session['status']}\n"
        f"pending confirmations: {len(pending)}"
    )


async def sber_answer_command(update, context) -> None:
    if not _authorized(update):
        await _deny(update)
        return

    raw = _payload(update)
    if "|" not in raw:
        await update.message.reply_text(
            "Формат: /sber_answer TURN_ID | твой ответ"
        )
        return

    turn_raw, answer = [part.strip() for part in raw.split("|", 1)]
    try:
        turn_id = int(turn_raw)
    except ValueError:
        await update.message.reply_text("TURN_ID должен быть числом.")
        return

    store = SberScreeningStore()
    if not store.approve_text(turn_id, answer):
        await update.message.reply_text(
            "Не удалось поставить ответ в очередь. Возможно, вопрос уже обработан."
        )
        return
    await update.message.reply_text(
        f"✅ Ответ для turn #{turn_id} подтверждён и поставлен в очередь."
    )


async def sber_callback(update, context) -> None:
    if not _authorized(update):
        await _deny(update)
        return

    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    store = SberScreeningStore()
    ok = False
    result = ""

    try:
        if data.startswith("sber_send:"):
            turn_id = int(data.split(":", 1)[1])
            ok = store.approve_suggestion(turn_id)
            result = "✅ Ответ подтверждён и поставлен в очередь."
        elif data.startswith("sber_skip:"):
            turn_id = int(data.split(":", 1)[1])
            ok = store.reject(turn_id)
            result = "⏭ Ответ не будет отправлен."
        elif data.startswith("sber_choice:"):
            _, turn_raw, index_raw = data.split(":", 2)
            turn_id = int(turn_raw)
            index = int(index_raw)
            ok = store.approve_button(turn_id, index)
            result = "✅ Выбор подтверждён и поставлен в очередь."
    except (ValueError, IndexError):
        ok = False

    if not ok:
        await query.answer("Уже обработано или данные устарели.", show_alert=True)
        return

    await query.answer("Готово")
    if query.message is not None:
        original = query.message.text or ""
        await query.edit_message_text(original + "\n\n" + result)


def install(module) -> None:
    """Add owner-only Sber screening commands and confirmation callbacks."""
    global _OWNER_CHAT_ID
    _OWNER_CHAT_ID = module.CHAT_ID

    from telegram.ext import CallbackQueryHandler, CommandHandler

    original_builder = module.ApplicationBuilder

    class SberScreeningApplicationBuilder(original_builder):
        def build(self):
            app = super().build()
            app.add_handler(CommandHandler("sber_arm", sber_arm_command))
            app.add_handler(CommandHandler("sber_status", sber_status_command))
            app.add_handler(CommandHandler("sber_answer", sber_answer_command))
            app.add_handler(
                CallbackQueryHandler(
                    sber_callback,
                    pattern=r"^sber_(?:send|skip|choice):",
                )
            )
            return app

    module.ApplicationBuilder = SberScreeningApplicationBuilder
