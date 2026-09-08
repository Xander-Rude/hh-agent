from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")


class TelegramRecommendationFilterTests(unittest.TestCase):
    def test_new_candidates_require_apply_decision(self) -> None:
        self.assertIn(
            '.where(bot_module.Evaluation.decision == "apply")',
            SOURCE,
        )

    def test_old_notified_cards_are_suppressed_when_latest_decision_is_not_apply(self) -> None:
        self.assertIn('if evaluation.decision != "apply":', SOURCE)
        self.assertIn("pending_not_recommended += 1", SOURCE)
        self.assertIn("pending suppressed", SOURCE)

    def test_summary_calls_new_cards_recommended(self) -> None:
        self.assertIn("Новых рекомендованных вакансий", SOURCE)


if __name__ == "__main__":
    unittest.main()
