from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def install(bot_module) -> None:
    """Keep source-aware vacancy links without breaking application-scoped callbacks."""

    native_builder = getattr(bot_module, "build_keyboard", None)
    if native_builder is not None and hasattr(bot_module, "_vacancy_open_target"):
        # Modern telegram_bot.py already handles source-aware URLs and keeps
        # callback identity scoped to Application ID when one is provided.
        return

    def build_keyboard(
        vacancy_id: int,
        application_id: int | None = None,
    ) -> InlineKeyboardMarkup:
        session = bot_module.SessionLocal()
        try:
            vacancy = session.get(bot_module.Vacancy, vacancy_id)
            if vacancy is None:
                source = "hh"
                url = "https://hh.ru"
            else:
                source = (vacancy.source or "hh").strip().lower()
                url = (vacancy.url or "").strip()

                if not url and source == "hh" and vacancy.hh_id:
                    url = f"https://hh.ru/vacancy/{vacancy.hh_id}"

            labels = {
                "hh": "🔗 Открыть HH",
                "yandex": "🔗 Открыть Yandex",
                "vk": "🔗 Открыть VK",
                "tbank": "🔗 Открыть Т-Банк",
                "ozon": "🔗 Открыть Ozon",
            }
            label = labels.get(source, f"🔗 Открыть {source.upper()}")

            target_id = (
                application_id
                if application_id is not None
                else vacancy_id
            )
            suffix = "_app" if application_id is not None else ""

            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Откликнуться",
                        callback_data=f"approve{suffix}:{target_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Пропустить",
                        callback_data=f"skip{suffix}:{target_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Компания в blacklist",
                        callback_data=(
                            f"blacklist_company{suffix}:{target_id}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        label,
                        url=url,
                    ),
                ],
            ]

            return InlineKeyboardMarkup(keyboard)
        finally:
            session.close()

    bot_module.build_keyboard = build_keyboard
