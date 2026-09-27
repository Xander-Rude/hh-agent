import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.cross_account import (
    application_is_confirmed_submitted,
    confirmed_other_account_application,
    normalized_account_key,
)


ROOT = Path(__file__).resolve().parents[1]


class CrossAccountApplicationTests(unittest.TestCase):
    def test_legacy_null_account_is_old(self) -> None:
        self.assertEqual(normalized_account_key(None), "old")

    def test_applied_and_already_applied_are_confirmed(self) -> None:
        for status in ("applied", "already_applied"):
            with self.subTest(status=status):
                app = SimpleNamespace(status=status, applied_at=None)
                self.assertTrue(application_is_confirmed_submitted(app))

    def test_skipped_and_failed_are_not_confirmed(self) -> None:
        for status in (
            "skipped",
            "notified",
            "apply_error",
            "manual_required",
            "company_blacklist",
        ):
            with self.subTest(status=status):
                app = SimpleNamespace(status=status, applied_at=None)
                self.assertFalse(application_is_confirmed_submitted(app))

    def test_applied_at_is_authoritative_even_if_status_is_stale(self) -> None:
        app = SimpleNamespace(
            status="notified",
            applied_at=object(),
        )
        self.assertTrue(application_is_confirmed_submitted(app))

    def test_other_account_applied_is_detected_but_skipped_is_ignored(self) -> None:
        rows = [
            SimpleNamespace(
                id=30,
                account_key="clean",
                status="notified",
                applied_at=None,
            ),
            SimpleNamespace(
                id=29,
                account_key="old",
                status="skipped",
                applied_at=None,
            ),
            SimpleNamespace(
                id=28,
                account_key="old",
                status="applied",
                applied_at=object(),
            ),
        ]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        result = confirmed_other_account_application(
            session,
            vacancy_id=123,
            account_key="clean",
        )

        self.assertIs(result, rows[2])

    def test_current_account_application_does_not_trigger_cross_account_guard(self) -> None:
        rows = [
            SimpleNamespace(
                id=31,
                account_key="clean",
                status="applied",
                applied_at=object(),
            ),
            SimpleNamespace(
                id=30,
                account_key="old",
                status="skipped",
                applied_at=None,
            ),
        ]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        result = confirmed_other_account_application(
            session,
            vacancy_id=123,
            account_key="clean",
        )

        self.assertIsNone(result)

    def test_telegram_requires_second_explicit_cross_account_confirmation(self) -> None:
        bot = (ROOT / "telegram_bot.py").read_text(
            encoding="utf-8",
            errors="replace",
        )
        pending = (ROOT / "telegram_bot_pending_patch.py").read_text(
            encoding="utf-8",
            errors="replace",
        )

        self.assertIn("approve_repeat_app:", bot)
        self.assertIn(
            'if action == "approve" and other_application is not None:',
            bot,
        )
        self.assertIn(
            'action in {"approve", "approve_repeat"}',
            bot,
        )
        self.assertIn(
            '"cross_account_repeat_confirmed": (',
            bot,
        )
        self.assertIn(
            "cross_account_application=cross_account_application",
            pending,
        )

    def test_old_skipped_is_not_a_reason_to_hide_clean_discovery(self) -> None:
        collector = (ROOT / "hh_collect.py").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn(
            "Application.account_key == account_key",
            collector,
        )
        self.assertIn(
            "HhVacancyDiscovery.account_key == account_key",
            collector,
        )


if __name__ == "__main__":
    unittest.main()
