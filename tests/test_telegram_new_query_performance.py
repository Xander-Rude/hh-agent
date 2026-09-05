import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")


class TelegramNewQueryPerformanceTests(unittest.TestCase):
    def test_new_filters_existing_applications_in_sql(self) -> None:
        self.assertIn("has_application = (", SOURCE)
        self.assertIn(".where(~has_application)", SOURCE)

    def test_new_uses_only_latest_evaluation(self) -> None:
        self.assertIn("latest_evaluation_id = (", SOURCE)
        self.assertIn(".scalar_subquery()", SOURCE)
        self.assertIn(
            "bot_module.Evaluation.id == latest_evaluation_id",
            SOURCE,
        )

    def test_candidate_loop_does_not_query_application_state_per_row(self) -> None:
        marker = 'print(\n                f"[TELEGRAM /new] new candidate rows:'
        candidate_section = SOURCE.split(marker, 1)[1]
        candidate_loop = candidate_section.split(
            "if sent_new + sent_pending + sent_manual == 0:",
            1,
        )[0]
        self.assertNotIn("get_application_state", candidate_loop)
        self.assertIn("[TELEGRAM /new] new sent:", candidate_loop)


if __name__ == "__main__":
    unittest.main()
