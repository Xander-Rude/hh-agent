from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def main() -> None:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit(
            "Telethon is not installed. Run setup_sber_screening.ps1 first."
        ) from exc

    api_id_raw = (os.getenv("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    if not api_id_raw or not api_hash:
        raise SystemExit(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in C:\\hh-agent\\.env first."
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise SystemExit("TELEGRAM_API_ID must be an integer.") from exc

    session_path = Path(
        os.getenv(
            "HH_SBER_TELEGRAM_SESSION",
            str(ROOT / "data" / "secrets" / "sber_user"),
        )
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), api_id, api_hash)
    client.start()
    me = client.get_me()
    username = getattr(me, "username", None)
    user_id = getattr(me, "id", None)
    print(
        "Telegram user session authorized: "
        f"id={user_id}, username=@{username or 'none'}"
    )
    print(f"Session stored locally under: {session_path}.session")
    client.disconnect()


if __name__ == "__main__":
    main()
