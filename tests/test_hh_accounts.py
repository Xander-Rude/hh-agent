from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import hh_accounts
from application_notifications import build_manual_required_message


class HHAccountTests(unittest.TestCase):
    def test_labels_are_stable(self) -> None:
        self.assertEqual(hh_accounts.account_label("clean"), "🟢 CLEAN")
        self.assertEqual(hh_accounts.account_label("old"), "⚪ OLD")

    def test_explicit_active_account_wins(self) -> None:
        with patch.dict(os.environ, {"HH_ACTIVE_ACCOUNT": "clean"}, clear=False):
            self.assertEqual(hh_accounts.active_apply_account().key, "clean")

    def test_old_is_safe_fallback_until_clean_login(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(hh_accounts, "has_saved_auth", return_value=False):
                self.assertEqual(hh_accounts.active_apply_account().key, "old")

    def test_clean_becomes_default_after_saved_login(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                hh_accounts,
                "has_saved_auth",
                side_effect=lambda account: getattr(account, "key", "") == "clean",
            ):
                self.assertEqual(hh_accounts.active_apply_account().key, "clean")

    def test_clean_resume_id_has_repository_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(hh_accounts, "read_account_state", return_value={}):
                self.assertEqual(
                    hh_accounts.account_resume_id("clean"),
                    "b5d6fbf3ff1124b2890039ed1f394633454535",
                )

    def test_manual_notification_contains_account_marker(self) -> None:
        message = build_manual_required_message(
            vacancy_title="Delivery Lead",
            company="Example",
            application_id=42,
            reason="manual",
            account_key="clean",
        )
        self.assertTrue(message.startswith("🟢 CLEAN"))


if __name__ == "__main__":
    unittest.main()
