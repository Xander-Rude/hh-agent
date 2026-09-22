import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
WORKER = (ROOT / "apply_worker.py").read_text(encoding="utf-8")
BOT = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")


class ApplyAccountSnapshotGuardTests(unittest.TestCase):
    def test_dispatcher_hh_queues_are_account_isolated(self) -> None:
        self.assertGreaterEqual(
            DISPATCHER.count(
                "Application.account_key == hh_worker.ACTIVE_ACCOUNT.key"
            ),
            2,
        )

    def test_worker_blocks_cross_account_last_mile(self) -> None:
        self.assertIn(
            "application_account != ACTIVE_ACCOUNT.key",
            WORKER,
        )
        self.assertIn(
            'return "account_mismatch"',
            WORKER,
        )

    def test_worker_prefers_approved_snapshot_cover_letter(self) -> None:
        self.assertIn(
            "snapshot = get_decision_snapshot(",
            WORKER,
        )
        self.assertIn(
            "snapshot.cover_letter_final",
            WORKER,
        )

    def test_approve_persists_decision_snapshot(self) -> None:
        self.assertIn(
            "return application",
            BOT,
        )
        self.assertIn(
            "ensure_decision_snapshot(",
            BOT,
        )
        self.assertIn(
            'if action.endswith("_app")',
            BOT,
        )


if __name__ == "__main__":
    unittest.main()
