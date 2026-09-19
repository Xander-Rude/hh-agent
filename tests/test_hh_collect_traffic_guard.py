import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hh_collect


class HHCollectTrafficGuardTests(unittest.TestCase):
    def test_response_history_cache_respects_ttl(self) -> None:
        now = datetime(2026, 9, 19, 18, 0, 0)
        fresh = SimpleNamespace(
            hh_response_checked_at=now - timedelta(hours=11, minutes=59)
        )
        stale = SimpleNamespace(
            hh_response_checked_at=now - timedelta(hours=12, minutes=1)
        )
        never_checked = SimpleNamespace(hh_response_checked_at=None)

        with patch.object(hh_collect, "RESPONSE_CHECK_TTL_HOURS", 12):
            self.assertTrue(
                hh_collect.response_check_cache_is_fresh(fresh, now=now)
            )
            self.assertFalse(
                hh_collect.response_check_cache_is_fresh(stale, now=now)
            )
            self.assertFalse(
                hh_collect.response_check_cache_is_fresh(
                    never_checked,
                    now=now,
                )
            )

    def test_captcha_cooldown_survives_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "hh_collect_cooldown.json"
            before = datetime.now(UTC)

            with (
                patch.object(hh_collect, "COOLDOWN_STATE_PATH", state_path),
                patch.object(hh_collect, "CAPTCHA_COOLDOWN_HOURS", 4),
            ):
                until = hh_collect.activate_hh_cooldown("captcha")
                remaining = hh_collect.hh_cooldown_remaining_seconds(
                    now=before
                )

                self.assertTrue(state_path.exists())
                self.assertGreaterEqual(remaining, 4 * 60 * 60)
                self.assertLess(remaining, 4 * 60 * 60 + 10)
                self.assertGreater(until, before)

    def test_expired_cooldown_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "hh_collect_cooldown.json"
            now = datetime.now(UTC)
            state_path.write_text(
                json.dumps(
                    {
                        "until": (now - timedelta(minutes=1)).isoformat(),
                        "reason": "captcha",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                hh_collect,
                "COOLDOWN_STATE_PATH",
                state_path,
            ):
                remaining = hh_collect.hh_cooldown_remaining_seconds(now=now)

            self.assertEqual(remaining, 0.0)
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
