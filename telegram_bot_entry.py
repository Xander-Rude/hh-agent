from __future__ import annotations

import telegram_bot
import telegram_cover_letter_patch
from telegram_bot_link_patch import install as install_link_patch
from telegram_bot_pending_patch import install as install_pending_patch
from telegram_cover_letter_output_patch import install as install_cover_output_patch
from telegram_queue_stats_patch import install as install_queue_stats_patch


install_link_patch(telegram_bot)
install_pending_patch(telegram_bot)
install_cover_output_patch(telegram_cover_letter_patch)
install_queue_stats_patch(telegram_bot)
telegram_cover_letter_patch.install(telegram_bot)


if __name__ == "__main__":
    telegram_bot.main()
