from __future__ import annotations

from pathlib import Path
import sys
import time


LOG_PATH = Path(__file__).resolve().parent / "logs" / "telegram.log"


def configure_windowless_output() -> None:
    """Send pythonw stdout/stderr to telegram.log without affecting console runs."""
    if sys.stdout is not None and sys.stderr is not None:
        return

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_stream = LOG_PATH.open(
        "a",
        encoding="utf-8",
        errors="backslashreplace",
        buffering=1,
    )
    if sys.stdout is None:
        sys.stdout = log_stream
    if sys.stderr is None:
        sys.stderr = log_stream


configure_windowless_output()

from telegram.error import NetworkError

import telegram_bot
import telegram_cover_letter_patch
from telegram_bot_link_patch import install as install_link_patch
from telegram_bot_pending_patch import install as install_pending_patch
from telegram_cover_letter_output_patch import install as install_cover_output_patch
from telegram_queue_stats_patch import install as install_queue_stats_patch


NETWORK_RETRY_DELAY_SECONDS = 30


install_link_patch(telegram_bot)
install_pending_patch(telegram_bot)
install_cover_output_patch(telegram_cover_letter_patch)
install_queue_stats_patch(telegram_bot)
telegram_cover_letter_patch.install(telegram_bot)


def run_forever() -> None:
    while True:
        try:
            telegram_bot.main()
            return
        except NetworkError as exc:
            print(
                "Telegram network unavailable: "
                f"{type(exc).__name__}: {exc}. "
                f"Retrying in {NETWORK_RETRY_DELAY_SECONDS}s.",
                flush=True,
            )
            time.sleep(NETWORK_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    run_forever()
