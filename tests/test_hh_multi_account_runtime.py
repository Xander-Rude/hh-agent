import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_APPLY = (ROOT / "background_apply.py").read_text(encoding="utf-8")
BACKGROUND_COMMON = (ROOT / "background_common.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
WORKER = (ROOT / "apply_worker.py").read_text(encoding="utf-8")
LOGIN = (ROOT / "hh_login.py").read_text(encoding="utf-8")
SESSION = (ROOT / "hh_session_guard.py").read_text(encoding="utf-8")
RESPONSE_SYNC = (ROOT / "response_sync_worker.py").read_text(encoding="utf-8")
TELEGRAM = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
TELEGRAM_PENDING = (
    ROOT / "telegram_bot_pending_patch.py"
).read_text(encoding="utf-8")


class HHMultiAccountRuntimeTests(unittest.TestCase):
    def test_apply_supervisor_runs_account_workers_in_parallel(self) -> None:
        self.assertIn("ThreadPoolExecutor", BACKGROUND_APPLY)
        self.assertIn("apply_accounts()", BACKGROUND_APPLY)
        self.assertIn('"HH_WORKER_ACCOUNT": account.key', BACKGROUND_APPLY)
        self.assertIn("HHProfileLock(account.key)", BACKGROUND_APPLY)

    def test_profiles_have_independent_locks_and_states(self) -> None:
        self.assertIn("def hh_profile_lock_path", BACKGROUND_COMMON)
        self.assertIn('f"hh_profile_{key}.lock"', BACKGROUND_COMMON)
        self.assertIn("def apply_state_path", BACKGROUND_COMMON)
        self.assertIn('f"apply_{key}.json"', BACKGROUND_COMMON)

    def test_worker_never_uses_active_account_when_pinned(self) -> None:
        self.assertIn("ACTIVE_ACCOUNT = account_for_worker()", WORKER)
        self.assertIn("Application.account_key", WORKER)
        self.assertIn("ACTIVE_ACCOUNT.key", WORKER)

    def test_dispatcher_requires_identity_verification(self) -> None:
        self.assertIn(
            "account=hh_worker.ACTIVE_ACCOUNT",
            DISPATCHER,
        )
        self.assertIn(
            "not session_status.identity_verified",
            DISPATCHER,
        )
        self.assertIn("ожидаемое резюме этого аккаунта", SESSION)

    def test_login_refuses_wrong_account_identity(self) -> None:
        self.assertIn("def _verify_expected_identity", LOGIN)
        self.assertIn(
            "Сессию не сохраняю",
            LOGIN,
        )

    def test_response_sync_is_account_locked_and_identity_safe(self) -> None:
        self.assertIn("HHProfileLock(account.key)", RESPONSE_SYNC)
        self.assertIn("expected_resume_id", RESPONSE_SYNC)
        self.assertIn("cross-account attribution", RESPONSE_SYNC)

    def test_telegram_cards_are_bound_to_application_and_account(self) -> None:
        self.assertIn(
            "Application.account_key == account.key",
            TELEGRAM_PENDING,
        )
        self.assertIn(
            "state.telegram_message_id",
            TELEGRAM_PENDING,
        )
        self.assertIn(
            "state.telegram_chat_id",
            TELEGRAM_PENDING,
        )
        self.assertIn(
            "current_status not in allowed_from[action]",
            TELEGRAM,
        )


if __name__ == "__main__":
    unittest.main()
