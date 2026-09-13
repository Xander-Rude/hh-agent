from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import telegram_watchdog


ROOT = Path(__file__).resolve().parents[1]


class TelegramWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    def test_fresh_running_heartbeat_is_healthy(self) -> None:
        state = {
            "status": "running",
            "updated_at": (self.now - timedelta(seconds=60)).isoformat(),
        }
        self.assertFalse(
            telegram_watchdog.is_heartbeat_stale(
                state,
                now=self.now,
                stale_seconds=180,
            )
        )

    def test_old_heartbeat_is_stale(self) -> None:
        state = {
            "status": "running",
            "updated_at": (self.now - timedelta(seconds=181)).isoformat(),
        }
        self.assertTrue(
            telegram_watchdog.is_heartbeat_stale(
                state,
                now=self.now,
                stale_seconds=180,
            )
        )

    def test_non_running_state_is_stale_even_with_fresh_timestamp(self) -> None:
        state = {
            "status": "stopped",
            "updated_at": self.now.isoformat(),
        }
        self.assertTrue(
            telegram_watchdog.is_heartbeat_stale(
                state,
                now=self.now,
                stale_seconds=180,
            )
        )

    def test_missing_or_invalid_timestamp_is_stale(self) -> None:
        self.assertTrue(
            telegram_watchdog.is_heartbeat_stale(
                {"status": "running"},
                now=self.now,
            )
        )
        self.assertTrue(
            telegram_watchdog.is_heartbeat_stale(
                {"status": "running", "updated_at": "not-a-date"},
                now=self.now,
            )
        )

    def test_task_installers_include_watchdog(self) -> None:
        install_source = (ROOT / "install_tasks.ps1").read_text(encoding="utf-8-sig")
        reinstall_source = (ROOT / "reinstall_telegram_task.ps1").read_text(
            encoding="utf-8-sig"
        )
        uninstall_source = (ROOT / "uninstall_tasks.ps1").read_text(
            encoding="utf-8-sig"
        )

        for source in (install_source, reinstall_source):
            self.assertIn("HH Agent - Telegram Watchdog", source)
            self.assertIn("telegram_watchdog.py", source)
            self.assertIn("New-TimeSpan -Minutes 1", source)

        self.assertIn("HH Agent - Telegram Watchdog", uninstall_source)

    def test_windows_powershell_installers_are_ascii_safe(self) -> None:
        for filename in ("install_tasks.ps1", "reinstall_telegram_task.ps1"):
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            source.encode("ascii")

    def test_entrypoint_installs_event_loop_heartbeat(self) -> None:
        source = (ROOT / "telegram_bot_entry.py").read_text(encoding="utf-8")
        patch_source = (ROOT / "telegram_heartbeat_patch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("install_heartbeat_patch(telegram_bot)", source)
        self.assertIn("application.create_task", patch_source)
        self.assertIn("HEARTBEAT_INTERVAL_SECONDS = 30", patch_source)


if __name__ == "__main__":
    unittest.main()
