from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")


class TelegramRecommendationFilterTests(unittest.TestCase):
    def test_new_candidates_allow_apply_and_review_decisions(self) -> None:
        self.assertIn('RECOMMENDED_DECISIONS = ("apply", "review")', SOURCE)
        self.assertIn(
            "bot_module.Evaluation.decision.in_(RECOMMENDED_DECISIONS)",
            SOURCE,
        )

    def test_old_notified_cards_are_suppressed_when_latest_decision_is_not_recommended(self) -> None:
        self.assertIn(
            "if evaluation.decision not in RECOMMENDED_DECISIONS:",
            SOURCE,
        )
        self.assertIn("pending_not_recommended += 1", SOURCE)

    def test_summary_is_account_specific(self) -> None:
        self.assertIn(
            "bot_module.account_label(account.key)",
            SOURCE,
        )
        self.assertIn(
            "f\"новых {stats['sent_new']}, \"",
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
