import unittest
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "telegram_bot.py"
).read_text(encoding="utf-8")


class TelegramManualRequiredRecoveryTests(unittest.TestCase):
    def test_new_has_manual_required_recovery_queue(self) -> None:
        self.assertIn(
            'Application.status == "manual_required"',
            SOURCE,
        )
        self.assertIn(
            'state.status != "manual_required"',
            SOURCE,
        )
        self.assertIn(
            "Требуют ручного действия: {sent_manual}",
            SOURCE,
        )

    def test_manual_card_can_be_resolved(self) -> None:
        self.assertIn(
            'callback_data=f"manual_done:{vacancy.id}"',
            SOURCE,
        )
        self.assertIn(
            'elif action == "manual_done":',
            SOURCE,
        )
        self.assertIn(
            'state.status = "applied"',
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
