from __future__ import annotations

import telegram_bot
from telegram_bot_link_patch import install


install(telegram_bot)


if __name__ == "__main__":
    telegram_bot.main()
