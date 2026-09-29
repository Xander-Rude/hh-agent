from __future__ import annotations

import unittest
from unittest.mock import patch

import telegram_sber_screening_patch as sber_patch


class _FakeStore:
    def __init__(self, states):
        self.states = list(states)
        self.calls = 0

    def get_turn(self, turn_id):
        self.calls += 1
        if not self.states:
            return None
        if len(self.states) == 1:
            return self.states[0]
        return self.states.pop(0)


class SberTelegramDeliveryTextTests(unittest.TestCase):
    def test_sent_is_reported_as_real_delivery(self):
        self.assertEqual(
            sber_patch._delivery_result_text({"status": "sent"}),
            "✅ Отправлено в ГигаРекрутер.",
        )

    def test_error_exposes_reason(self):
        text = sber_patch._delivery_result_text(
            {"status": "error", "reason": "boom"}
        )
        self.assertIn("❌ Не отправлено", text)
        self.assertIn("boom", text)


class SberTelegramWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_returns_sent_terminal_state(self):
        store = _FakeStore(
            [
                {"status": "approved"},
                {"status": "approved"},
                {"status": "sent"},
            ]
        )
        with patch.object(sber_patch, "SBER_CONFIRM_POLL_SECONDS", 0):
            result = await sber_patch._wait_turn_terminal(store, 7)
        self.assertEqual(result["status"], "sent")
        self.assertGreaterEqual(store.calls, 3)


if __name__ == "__main__":
    unittest.main()
