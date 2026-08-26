from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def install(bot_module) -> None:
    """Make the vacancy-open button use the actual vacancy source and URL."""

    def build_keyboard(vacancy_id: int) -> InlineKeyboardMarkup:
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
            }
            label = labels.get(source, f"🔗 Открыть {source.upper()}")

            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Откликнуться",
                        callback_data=f"approve:{vacancy_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Пропустить",
                        callback_data=f"skip:{vacancy_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Компания в blacklist",
                        callback_data=f"blacklist_company:{vacancy_id}",
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
