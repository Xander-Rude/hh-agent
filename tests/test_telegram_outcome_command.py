import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")


class TelegramOutcomeCommandTests(unittest.TestCase):
    def test_outcome_command_requires_operator_chat(self) -> None:
        self.assertIn(
            'update.effective_chat.id != CHAT_ID',
            SOURCE,
        )
        self.assertIn(
            '"/outcome доступна только операторскому Telegram-чату."',
            SOURCE,
        )

    def test_outcome_command_requires_explicit_attribution(self) -> None:
        self.assertIn(
            "len(args) < 3",
            SOURCE,
        )
        self.assertIn(
            "attribution not in OUTCOME_ATTRIBUTIONS",
            SOURCE,
        )

    def test_outcome_command_records_user_confirmed_stage(self) -> None:
        self.assertIn(
            'source="telegram_manual"',
            SOURCE,
        )
        self.assertIn(
            'confidence="user_confirmed"',
            SOURCE,
        )
        self.assertIn(
            'CommandHandler("outcome", outcome_command)',
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
