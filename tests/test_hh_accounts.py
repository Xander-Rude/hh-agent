from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
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

    def test_worker_account_is_pinned_by_env(self) -> None:
        with patch.dict(
            os.environ,
            {"HH_WORKER_ACCOUNT": "old", "HH_ACTIVE_ACCOUNT": "clean"},
            clear=False,
        ):
            self.assertEqual(hh_accounts.account_for_worker().key, "old")

    def test_apply_accounts_include_both_saved_profiles(self) -> None:
        with patch.dict(
            os.environ,
            {"HH_ACTIVE_ACCOUNT": "clean"},
            clear=False,
        ):
            with patch.object(
                hh_accounts,
                "has_saved_auth",
                return_value=True,
            ):
                self.assertEqual(
                    [item.key for item in hh_accounts.apply_accounts()],
                    ["clean", "old"],
                )

    def test_clean_resume_id_has_repository_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(hh_accounts, "read_account_state", return_value={}):
                self.assertEqual(
                    hh_accounts.account_resume_id("clean"),
                    "b5d6fbf3ff1124b2890039ed1f394633454535",
                )


    def test_write_account_state_sets_activation_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(hh_accounts, "STATE_DIR", Path(temp_dir)):
                hh_accounts.write_account_state(
                    "clean",
                    authenticated=True,
                    resume_ids=["resume-1"],
                )
                first = hh_accounts.read_account_state("clean")
                activated_at = first.get("activated_at")
                self.assertTrue(activated_at)
                datetime.fromisoformat(activated_at)

                hh_accounts.write_account_state(
                    "clean",
                    authenticated=True,
                    resume_ids=["resume-1"],
                )
                second = hh_accounts.read_account_state("clean")
                self.assertEqual(second.get("activated_at"), activated_at)

    def test_activation_falls_back_to_state_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(hh_accounts, "STATE_DIR", Path(temp_dir)):
                state_path = hh_accounts.get_account("clean").state_path
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    '{"account_key":"clean","authenticated":true}',
                    encoding="utf-8",
                )
                self.assertIsNotNone(
                    hh_accounts.account_activated_at("clean")
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
