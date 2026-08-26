import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
PRODUCTION_PATCH = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "telegram_bot_entry.py").read_text(encoding="utf-8")


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

    def test_production_override_also_recovers_manual_required(self) -> None:
        self.assertIn(
            'bot_module.Application.status == "manual_required"',
            PRODUCTION_PATCH,
        )
        self.assertIn(
            'state.status != "manual_required"',
            PRODUCTION_PATCH,
        )
        self.assertIn(
            "bot_module.build_manual_required_message(vacancy, state)",
            PRODUCTION_PATCH,
        )
        self.assertIn(
            "bot_module.build_manual_required_keyboard(vacancy)",
            PRODUCTION_PATCH,
        )
        self.assertIn(
            "Требуют ручного действия: {sent_manual}",
            PRODUCTION_PATCH,
        )
        self.assertNotIn(
            "Нет новых вакансий и нет карточек без решения.",
            PRODUCTION_PATCH,
        )

    def test_production_entrypoint_installs_pending_patch(self) -> None:
        self.assertIn("install_pending_patch(telegram_bot)", ENTRYPOINT)


if __name__ == "__main__":
    unittest.main()
