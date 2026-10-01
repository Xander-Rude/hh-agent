from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "telegram_bot.py").read_text(
    encoding="utf-8"
)


class TelegramAccessControlTests(unittest.TestCase):
    def test_telegram_chat_id_is_required_at_runtime(self) -> None:
        main_source = SOURCE.split("def main() -> None:", 1)[1]
        self.assertIn("if CHAT_ID is None:", main_source)
        self.assertIn(
            'raise RuntimeError("В .env отсутствует TELEGRAM_CHAT_ID")',
            main_source,
        )

    def test_owner_guard_stops_foreign_updates(self) -> None:
        self.assertIn("async def operator_chat_guard(", SOURCE)
        self.assertIn("raise ApplicationHandlerStop", SOURCE)
        self.assertIn(
            "int(update.effective_chat.id) != int(CHAT_ID)",
            SOURCE,
        )

    def test_guard_runs_before_all_other_handlers(self) -> None:
        self.assertIn(
            "TypeHandler(Update, operator_chat_guard)",
            SOURCE,
        )
        self.assertIn("group=-1", SOURCE)

    def test_public_mode_copy_is_removed(self) -> None:
        self.assertNotIn("Access mode: public", SOURCE)
        self.assertNotIn("Режим доступа: публичный", SOURCE)


if __name__ == "__main__":
    unittest.main()
