"""Owner-only batch review of HH employer questionnaire drafts in Telegram."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db import Application, SessionLocal, Vacancy
from app.clean_live_guard import clean_eligibility
from app.hh_questionnaire import QuestionnaireStore, card_text

_OWNER_CHAT_ID = None


def _allowed(update) -> bool:
    return (_OWNER_CHAT_ID is not None and update.effective_chat is not None
            and int(update.effective_chat.id) == int(_OWNER_CHAT_ID))


def _vacancy(application_id: int):
    with SessionLocal() as db:
        app = db.get(Application, application_id)
        if app is None:
            return None
        vacancy = db.get(Vacancy, app.vacancy_id)
        if vacancy is None:
            return None
        return {
            "application_id": app.id,
            "account_key": app.account_key,
            "status": app.status,
            "source": vacancy.source,
            "title": vacancy.title,
            "company": vacancy.company or "",
            "url": vacancy.url,
        }


def _keyboard(application_id: int, url: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить всё", callback_data=f"hhq_ok:{application_id}"),
         InlineKeyboardButton("✏️ Изменить ответы", callback_data=f"hhq_edit:{application_id}")],
        [InlineKeyboardButton("❌ Не отправлять", callback_data=f"hhq_reject:{application_id}")],
        [InlineKeyboardButton("Открыть HH", url=url)],
    ])


async def _show_draft(message, application_id: int):
    record = QuestionnaireStore().get(application_id)
    vacancy = _vacancy(application_id)
    if not record or not vacancy:
        await message.reply_text("Анкета по такому Application ID не найдена.")
        return
    if record["status"] == "rejected":
        await message.reply_text("Анкета отклонена. Автоматически не отправляется.")
        return
    if record["status"] == "sent":
        await message.reply_text("Анкета уже отправлена и подтверждена HH.")
        return
    if record["status"] == "approved":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await message.reply_text(
            f"✅ Анкета #{application_id} согласована. "
            "Если HH ранее попросил изменить видимость резюме, "
            "сделай это вручную и затем повтори проверку отклика.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 Повторить отправку после исправления",
                    callback_data=f"hhq_retry:{application_id}",
                )],
                [InlineKeyboardButton("Открыть HH", url=vacancy["url"])],
            ]),
        )
        return
    text = card_text(application_id, vacancy["title"], vacancy["company"], record)
    await message.reply_text(
        text, reply_markup=_keyboard(application_id, vacancy["url"]),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def hhq_command(update, context):
    if not _allowed(update):
        return
    if context.args:
        try:
            application_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Формат: /hhq APPLICATION_ID")
            return
    else:
        with QuestionnaireStore()._connect() as db:
            row = db.execute("SELECT application_id FROM questionnaire WHERE status IN ('pending','approved') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            await update.message.reply_text("Анкет на согласовании нет.")
            return
        application_id = int(row["application_id"])
    await _show_draft(update.message, application_id)


def _release_application(application_id: int, account_key: str, *, expected_status: str = "manual_required") -> tuple[bool, str]:
    from hh_accounts import account_resume_id
    with SessionLocal() as db:
        app = db.get(Application, application_id)
        if app is None or app.account_key != account_key or app.status != expected_status:
            return False, "Состояние отклика изменилось. Ничего не отправляю."
        vac = db.get(Vacancy, app.vacancy_id)
        if vac is None or (vac.source or "hh") != "hh":
            return False, "Это не форма HH."
        expected_resume = account_resume_id(account_key)
        if expected_resume and app.selected_resume_id != expected_resume:
            return False, "Привязка резюме не совпадает с аккаунтом. Ничего не отправляю."
        if account_key == "clean":
            eligibility = clean_eligibility(db, vac.id)
            if not eligibility.eligible:
                return False, "CLEAN guard не разрешает этот отклик."
        if not (app.cover_letter or "").strip():
            return False, "У отклика отсутствует подготовленное сопроводительное письмо."
        app.status = "approved"
        db.commit()
    return True, "✅ Анкета согласована. Отклик передан штатному worker; до его проверки HH статус applied не выставляется."


async def hhq_callback(update, context):
    if not _allowed(update):
        if update.callback_query:
            await update.callback_query.answer("Недоступно.", show_alert=True)
        return
    query = update.callback_query
    if not query:
        return
    try:
        action, raw = (query.data or "").split(":", 1)
        application_id = int(raw)
    except ValueError:
        await query.answer("Некорректная кнопка.", show_alert=True)
        return
    store = QuestionnaireStore()
    record = store.get(application_id)
    vacancy = _vacancy(application_id)
    if not record or not vacancy or record["account_key"] != vacancy["account_key"]:
        await query.answer("Данные анкеты не найдены или аккаунт изменился.", show_alert=True)
        return

    if action == "hhq_edit":
        if record["status"] != "pending":
            await query.answer("Анкета уже обработана.", show_alert=True)
            return
        await query.answer()
        from telegram import ForceReply
        await query.message.reply_text(
            f"✏️ Правка анкеты HH #{application_id}\n"
            "Ответь на это сообщение строками «номер | новый ответ». "
            "Можно исправить несколько ответов одним сообщением:\n"
            "1 | Первый ответ\n3 | Третий ответ\n"
            "Для выбора используй точный текст варианта из анкеты.",
            reply_markup=ForceReply(selective=True, input_field_placeholder="1 | Мой вариант ответа"),
        )
        return

    if action == "hhq_reject":
        ok = store.transition(application_id, "pending", "rejected")
        await query.answer("Не отправляем." if ok else "Анкета уже обработана.", show_alert=not ok)
        if ok:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "hhq_ok":
        if record["status"] != "pending" or not store.valid_answers(record["questions"], record["answers"]):
            await query.answer("Сначала заполни ответы без подтверждённых фактов.", show_alert=True)
            return
        if vacancy["status"] != "manual_required":
            await query.answer("Статус отклика изменился. Ничего не отправлено.", show_alert=True)
            return
        if not store.transition(application_id, "pending", "approved"):
            await query.answer("Анкета уже обработана.", show_alert=True)
            return
        ok, reason = _release_application(application_id, record["account_key"])
        if not ok:
            store.transition(application_id, "approved", "pending", reason)
            await query.answer(reason[:190], show_alert=True)
            return
        await query.answer("Согласовано.")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(reason)
        return

    if action == "hhq_retry":
        if record["status"] != "approved":
            await query.answer("Нет согласованной анкеты.", show_alert=True)
            return
        ok, reason = _release_application(application_id, record["account_key"])
        await query.answer(reason[:190], show_alert=not ok)
        return


async def hhq_custom_reply(update, context):
    if not _allowed(update):
        return
    message = update.message
    if not message or not message.reply_to_message:
        return
    match = re.match(r"^✏️ Правка анкеты HH #(\d+)\b", message.reply_to_message.text or "")
    if not match:
        return
    application_id = int(match.group(1))
    updates = {}
    for line in (message.text or "").strip().splitlines():
        part = re.match(r"^\s*(\d{1,2})\s*\|\s*(.+?)\s*$", line)
        if not part:
            await message.reply_text("Формат: 1 | Новый ответ. Каждая правка — отдельная строка.")
            return
        updates[int(part.group(1)) - 1] = part.group(2)
    if not updates or not QuestionnaireStore().edit(application_id, updates):
        await message.reply_text("Не получилось сохранить. Проверь номера вопросов и точные варианты для выбора.")
        return
    await _show_draft(message, application_id)


def install(module):
    """Register before the generic callback handler, but after Sber's builder."""
    global _OWNER_CHAT_ID
    _OWNER_CHAT_ID = module.CHAT_ID
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    parent_builder = module.ApplicationBuilder

    class QuestionnaireBuilder(parent_builder):
        def build(self):
            app = super().build()
            app.add_handler(CommandHandler("hhq", hhq_command))
            app.add_handler(CallbackQueryHandler(
                hhq_callback,
                pattern=r"^hhq_(?:ok|edit|reject|retry):\d+$",
            ))
            # Sber uses group=1 for generic ForceReply. A distinct later group
            # lets our replies be processed without swallowing Sber's replies.
            app.add_handler(
                MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, hhq_custom_reply),
                group=2,
            )
            return app
    module.ApplicationBuilder = QuestionnaireBuilder
