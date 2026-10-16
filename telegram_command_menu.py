"""Owner-only Telegram command menu for hh-agent.

Keep this list in sync with all CommandHandler registrations in the bot and
its installed command patches; tests verify that no command is omitted.
"""
from __future__ import annotations

from telegram import BotCommand, BotCommandScopeChat, MenuButtonCommands
from telegram.error import TelegramError


OWNER_COMMANDS = (
    BotCommand("start", "Справка по боту"),
    BotCommand("new", "Новые вакансии и карточки решений"),
    BotCommand("health", "Проверить здоровье агента"),
    BotCommand("status", "Статус пайплайна и очередей"),
    BotCommand("stats", "Статистика откликов"),
    BotCommand("tech", "Техническая диагностика"),
    BotCommand("run", "Запустить пайплайн сейчас"),
    BotCommand("hh_resume", "Снять паузу CAPTCHA для аккаунта"),
    BotCommand("outcome", "Записать этап по отклику"),
    BotCommand("sber_status", "Статус скрининга Сбера"),
    BotCommand("sber_arm", "Активировать скрининг Сбера"),
    BotCommand("sber_answer", "Ввести ответ для скрининга"),
    BotCommand("hhq", "Согласовать вопросы анкеты HH"),
    BotCommand("intel", "Сведения о компании и контактах"),
    BotCommand("person", "Добавить контактное лицо"),
    BotCommand("contact", "Добавить контакт человека"),
    BotCommand("note", "Сохранить заметку"),
    BotCommand("entry", "Отметить точку входа в компанию"),
    BotCommand("hunt", "Подготовить адресный выход"),
    BotCommand("outreach", "Записать результат контакта"),
)


async def register_owner_command_menu(application, owner_chat_id: int) -> None:
    """Install Telegram's native Menu button only in the operator chat.

    Menu API errors must not prevent the bot from polling or approving actions.
    A subsequent bot restart will retry registration.
    """
    owner_scope = BotCommandScopeChat(chat_id=owner_chat_id)
    try:
        await application.bot.set_my_commands(
            commands=OWNER_COMMANDS,
            scope=owner_scope,
        )
    except TelegramError as exc:
        print(
            f"[TELEGRAM MENU] set_my_commands failed: {type(exc).__name__}: {exc}",
            flush=True,
        )

    try:
        await application.bot.set_chat_menu_button(
            chat_id=owner_chat_id,
            menu_button=MenuButtonCommands(),
        )
    except TelegramError as exc:
        print(
            f"[TELEGRAM MENU] set_chat_menu_button failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
