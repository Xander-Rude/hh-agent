from __future__ import annotations

import unittest

from sber_screening_web_worker import (
    TelegramWebGigaClient,
    _choice_keyboard,
    _clean_message_text,
    _pending_keyboard,
)


class _FakeWeb(TelegramWebGigaClient):
    def __init__(self, message_id: int | None):
        super().__init__()
        self.message_id = message_id

    async def latest_incoming(self):
        if self.message_id is None:
            return None
        return {
            "external_message_id": self.message_id,
            "question": "Q",
            "options": [],
        }


class SberScreeningWebHelpersTest(unittest.TestCase):
    def test_clean_message_text_strips_only_trailing_time(self):
        self.assertEqual(
            _clean_message_text("Вопрос\nс числом 19:12 внутри\n19:13"),
            "Вопрос\nс числом 19:12 внутри",
        )

    def test_choice_keyboard_preserves_index(self):
        keyboard = _choice_keyboard(17, ["Первый", "Второй"])
        rows = keyboard["inline_keyboard"]
        self.assertEqual(rows[0][0]["callback_data"], "sber_choice:17:0")
        self.assertEqual(rows[1][0]["callback_data"], "sber_choice:17:1")
        self.assertEqual(rows[-1][0]["callback_data"], "sber_skip:17")

    def test_pending_keyboard_does_not_offer_send_without_answer(self):
        keyboard = _pending_keyboard(9, False)
        callbacks = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(callbacks, ["sber_skip:9"])


class SberScreeningWebSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_current_turn_guard_accepts_same_message(self):
        web = _FakeWeb(123)
        await web._assert_turn_is_current(123)

    async def test_current_turn_guard_rejects_changed_question(self):
        web = _FakeWeb(124)
        with self.assertRaisesRegex(RuntimeError, "stale answer was not sent"):
            await web._assert_turn_is_current(123)

    async def test_current_turn_guard_rejects_missing_question(self):
        web = _FakeWeb(None)
        with self.assertRaisesRegex(RuntimeError, "no incoming message"):
            await web._assert_turn_is_current(123)


if __name__ == "__main__":
    unittest.main()
