from __future__ import annotations

import telegram_bot
from telegram_bot_link_patch import install as install_link_patch
from telegram_bot_pending_patch import install as install_pending_patch


install_link_patch(telegram_bot)
install_pending_patch(telegram_bot)


if __name__ == "__main__":
    telegram_bot.main()
