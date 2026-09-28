import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")


class TelegramNewQueryPerformanceTests(unittest.TestCase):
    def test_new_filters_existing_applications_per_account_in_sql(self) -> None:
        self.assertIn("has_account_application = (", SOURCE)
        self.assertIn(".where(~has_account_application)", SOURCE)
        self.assertIn(
            "bot_module.Application.account_key == account.key",
            SOURCE,
        )

    def test_new_uses_only_latest_evaluation(self) -> None:
        self.assertIn("latest_evaluation_id = (", SOURCE)
        self.assertIn(".scalar_subquery()", SOURCE)
        self.assertIn(
            "bot_module.Evaluation.id == latest_evaluation_id",
            SOURCE,
        )

    def test_candidate_loop_does_not_query_application_state_per_row(self) -> None:
        self.assertNotIn("get_application_state", SOURCE)
        self.assertIn("new candidate rows", SOURCE)
        self.assertIn(
            "bot_module.create_notification_state(",
            SOURCE,
        )

    def test_new_does_not_block_card_delivery_on_human_cover_writer(self) -> None:
        self.assertGreaterEqual(
            SOURCE.count("humanize_cover_letter=False"),
            2,
        )
        self.assertIn("_schedule_cover_upgrade(", SOURCE)
        self.assertIn("asyncio.to_thread(generate_and_render)", SOURCE)
        self.assertIn("HH_COVER_BACKGROUND_CONCURRENCY", SOURCE)


if __name__ == "__main__":
    unittest.main()
