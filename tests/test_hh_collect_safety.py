import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import hh_collect as collect


class HHCollectSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ttl = collect.HH_RESPONSE_CHECK_TTL_HOURS
        self.original_cooldown_hours = collect.HH_CAPTCHA_COOLDOWN_HOURS
        self.original_cooldown_path = collect.CAPTCHA_COOLDOWN_PATH

    def tearDown(self) -> None:
        collect.HH_RESPONSE_CHECK_TTL_HOURS = self.original_ttl
        collect.HH_CAPTCHA_COOLDOWN_HOURS = self.original_cooldown_hours
        collect.CAPTCHA_COOLDOWN_PATH = self.original_cooldown_path

    def test_recent_history_check_does_not_need_remote_open(self) -> None:
        collect.HH_RESPONSE_CHECK_TTL_HOURS = 24
        vacancy = SimpleNamespace(
            hh_response_checked_at=collect.utc_now_naive() - timedelta(hours=2)
        )
        application = SimpleNamespace(status="pending")

        self.assertFalse(
            collect.needs_remote_response_check(vacancy, application)
        )

    def test_stale_history_check_is_rechecked(self) -> None:
        collect.HH_RESPONSE_CHECK_TTL_HOURS = 24
        vacancy = SimpleNamespace(
            hh_response_checked_at=collect.utc_now_naive() - timedelta(hours=25)
        )
        application = SimpleNamespace(status="pending")

        self.assertTrue(
            collect.needs_remote_response_check(vacancy, application)
        )

    def test_terminal_application_status_never_reopens_old_vacancy(self) -> None:
        collect.HH_RESPONSE_CHECK_TTL_HOURS = 24
        vacancy = SimpleNamespace(hh_response_checked_at=None)
        application = SimpleNamespace(status="applied")

        self.assertFalse(
            collect.needs_remote_response_check(vacancy, application)
        )

    def test_captcha_cooldown_persists_between_runs(self) -> None:
        collect.HH_CAPTCHA_COOLDOWN_HOURS = 4

        with tempfile.TemporaryDirectory() as tmp_dir:
            collect.CAPTCHA_COOLDOWN_PATH = (
                Path(tmp_dir) / "hh_captcha_cooldown_until.txt"
            )

            activated = collect.activate_captcha_cooldown()
            remaining = collect.captcha_cooldown_remaining_seconds()

            self.assertGreater(activated, 0)
            self.assertGreater(remaining, 3 * 3600)
            self.assertTrue(collect.CAPTCHA_COOLDOWN_PATH.exists())


if __name__ == "__main__":
    unittest.main()
