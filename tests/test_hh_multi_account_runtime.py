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
DB = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
DECISION = (ROOT / "app" / "decision_snapshot.py").read_text(encoding="utf-8")
OLD_QUEUE = (ROOT / "old_auto_queue.py").read_text(encoding="utf-8")
PIPELINE = (ROOT / "background_pipeline.py").read_text(encoding="utf-8")
CLEAN_SHADOW = (ROOT / "clean_shadow.py").read_text(encoding="utf-8")


class HHMultiAccountRuntimeTests(unittest.TestCase):
    def test_apply_supervisor_runs_account_workers_in_parallel(self) -> None:
        self.assertIn("ThreadPoolExecutor", BACKGROUND_APPLY)
        self.assertIn("apply_accounts()", BACKGROUND_APPLY)
        self.assertIn('"HH_WORKER_ACCOUNT": account.key', BACKGROUND_APPLY)
        self.assertIn("HHProfileLock(account.key)", BACKGROUND_APPLY)

    def test_apply_isolated_by_profile_not_global_pipeline_lock(self) -> None:
        self.assertNotIn("with AgentLock():", BACKGROUND_APPLY)
        self.assertIn("HHProfileLock(account.key)", BACKGROUND_APPLY)
        pipeline = (ROOT / "background_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("with _agent_lock_with_retry():", pipeline)
        resume = (ROOT / "background_resume_raise.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("with AgentLock():", resume)

    def test_old_queue_runs_after_scoring_and_shadow(self) -> None:
        self.assertGreater(
            PIPELINE.index('log("OLD scored auto queue seed/promote")'),
            PIPELINE.index('log("4/5 clean_shadow.py")'),
        )
        self.assertIn("old_auto_eligibility", OLD_QUEUE)
        self.assertIn('"eligible"', OLD_QUEUE)
        self.assertIn('"ineligible"', OLD_QUEUE)

    def test_old_semantic_shadow_is_score_bounded(self) -> None:
        self.assertIn("OLD_AUTO_MIN_SCORE", CLEAN_SHADOW)
        self.assertIn("[OLD SHADOW QUEUE]", CLEAN_SHADOW)
        self.assertIn(
            "Evaluation.score >= OLD_AUTO_MIN_SCORE",
            CLEAN_SHADOW,
        )

    def test_old_shadow_does_not_enqueue_clean_cover_letters(self) -> None:
        self.assertIn("has_clean_discovery", CLEAN_SHADOW)
        self.assertIn(
            'HhVacancyDiscovery.account_key == "clean"',
            CLEAN_SHADOW,
        )

    def test_old_discovery_does_not_auto_queue_before_scoring(self) -> None:
        collector = (ROOT / "hh_collect.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "from old_auto_queue import ensure_old_auto_application",
            collector,
        )

    def test_old_dispatcher_rechecks_auto_gate_before_submit(self) -> None:
        self.assertIn("old_auto_eligibility", DISPATCHER)
        self.assertIn("[OLD AUTO GATE] skip", DISPATCHER)

    def test_old_apply_never_waits_inside_profile_lock(self) -> None:
        self.assertIn('"1" if ACTIVE_ACCOUNT.key == "old" else "10"', WORKER)
        self.assertIn("max_wait_seconds=0", WORKER)

    def test_deploy_reserves_all_hh_profiles(self) -> None:
        holder = (ROOT / "deploy" / "agent_lock_holder.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("HHProfileLock", holder)
        self.assertIn("for account in all_accounts()", holder)


    def test_apply_scheduler_polls_every_minute(self) -> None:
        install = (ROOT / "install_tasks.ps1").read_text(encoding="utf-8")
        hardener = (ROOT / "deploy" / "harden_scheduled_tasks.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("RepetitionInterval (New-TimeSpan -Minutes 1)", install)
        self.assertIn("[OK] Apply cadence: every 1 minute", hardener)

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

    def test_database_enforces_one_application_per_account_vacancy(self) -> None:
        self.assertIn(
            "uq_applications_vacancy_account",
            DB,
        )
        self.assertIn(
            "ON applications(vacancy_id, account_key)",
            DB,
        )

    def test_open_hh_applications_are_rebound_to_account_resume(self) -> None:
        self.assertIn(
            "def _backfill_hh_application_resume_bindings",
            DB,
        )
        self.assertIn(
            "selected_resume_id=:resume_id",
            DB,
        )

    def test_decision_snapshot_records_bound_account_resume(self) -> None:
        self.assertIn(
            "bound_resume_id = account_resume_id(account_key)",
            DECISION,
        )
        self.assertIn(
            "selected_resume_id=application.selected_resume_id",
            DECISION,
        )

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
