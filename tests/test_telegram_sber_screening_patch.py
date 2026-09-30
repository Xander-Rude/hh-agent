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


class _ReplyTarget:
    text = "✏️ Свой ответ для turn #42\nОтветь на это сообщение одним сообщением."


class _CustomReplyMessage:
    def __init__(self, text):
        self.text = text
        self.reply_to_message = _ReplyTarget()
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class _Chat:
    id = 123


class _CustomReplyUpdate:
    def __init__(self, text):
        self.effective_chat = _Chat()
        self.message = _CustomReplyMessage(text)
        self.callback_query = None


class _ApproveStore:
    def __init__(self):
        self.approved = []

    def approve_text(self, turn_id, text):
        self.approved.append((turn_id, text))
        return True

    def get_turn(self, turn_id):
        return {"status": "sent"}


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


class SberTelegramCustomReplyTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_reply_turn_id_is_read_from_force_reply_prompt(self):
        message = _CustomReplyMessage("мой ответ")
        self.assertEqual(sber_patch._custom_reply_turn_id(message), 42)

    async def test_plain_reply_is_queued_as_custom_answer(self):
        update = _CustomReplyUpdate("Мой обычный ответ без команды")
        store = _ApproveStore()

        with patch.object(sber_patch, "_OWNER_CHAT_ID", 123), patch.object(
            sber_patch,
            "SberScreeningStore",
            return_value=store,
        ):
            await sber_patch.sber_custom_reply(update, None)

        self.assertEqual(
            store.approved,
            [(42, "Мой обычный ответ без команды")],
        )
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn(
            "✅ Отправлено в ГигаРекрутер.",
            update.message.replies[0][0],
        )


if __name__ == "__main__":
    unittest.main()
