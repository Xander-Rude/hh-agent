from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.sber_screening_store import SberScreeningStore
from sber_screening_web_worker import (
    TelegramWebGigaClient,
    _handle_new_message,
    _choice_keyboard,
    _clean_message_text,
    _is_terminal_message,
    _pending_keyboard,
    _screening_start_vacancy_title,
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
        self.assertEqual(
            callbacks,
            ["sber_custom:9", "sber_skip:9"],
        )

    def test_pending_keyboard_offers_send_and_custom_answer(self):
        keyboard = _pending_keyboard(9, True)
        callbacks = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(
            callbacks,
            ["sber_send:9", "sber_custom:9", "sber_skip:9"],
        )

    def test_terminal_message_requires_completion_markers(self):
        self.assertTrue(
            _is_terminal_message(
                "Спасибо за интервью! Я передам ваше резюме и итоги диалога рекрутеру."
            )
        )
        self.assertFalse(_is_terminal_message("Спасибо за ответ! Следующий вопрос."))

    def test_screening_start_title_is_parsed_from_giga_greeting(self):
        self.assertEqual(
            _screening_start_vacancy_title(
                "Здравствуйте! Получил Ваш отклик на позицию Delivery Manager (Прайм).\n\n"
                "Будет удобно ответить на вопросы?"
            ),
            "Delivery Manager (Прайм)",
        )
        self.assertIsNone(
            _screening_start_vacancy_title("По какой вакансии продолжить диалог?")
        )

    def test_screening_start_title_is_parsed_from_resume_message(self):
        self.assertEqual(
            _screening_start_vacancy_title(
                "Давайте продолжим общение по вакансии Project Manager(AI-Agents). "
                "Мы остановились на этом месте:\n"
                "Здравствуйте! Получил Ваш отклик на позицию Project Manager(AI-Agents)."
            ),
            "Project Manager(AI-Agents)",
        )


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


class _FakeChoiceWeb:
    async def latest_incoming(self):
        return {
            "external_message_id": 12345,
            "question": "Выберите вакансию",
            "options": ["Первая", "Вторая"],
        }


class _FakeTerminalWeb:
    async def latest_incoming(self):
        return {
            "external_message_id": 777,
            "question": (
                "Спасибо за интервью! Я передам ваше резюме и итоги "
                "нашего диалога рекрутеру для дальнейшего рассмотрения."
            ),
            "options": [],
        }


class _FakeGreetingWeb:
    async def latest_incoming(self):
        return {
            "external_message_id": 888,
            "question": (
                "Здравствуйте! Получил Ваш отклик на позицию Delivery Manager (Прайм).\n\n"
                "Будет удобно прямо сейчас ответить на несколько вопросов?"
            ),
            "options": [],
        }


class _FakeMissedExternalGreetingWeb:
    async def latest_incoming(self):
        return {
            "external_message_id": 902,
            "question": "Каков ваш текущий статус занятости?",
            "options": [],
        }

    async def recent_incoming(self, limit=20):
        return [
            {
                "external_message_id": 901,
                "question": (
                    "Здравствуйте! Получил Ваш отклик на позицию "
                    "Руководитель продукта (GigaCode).\n\n"
                    "Будет удобно ответить на несколько вопросов?"
                ),
                "options": [],
            },
            await self.latest_incoming(),
        ]


class SberScreeningTerminalTest(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_message_completes_session_without_llm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SberScreeningStore(Path(temp_dir) / "sber.sqlite3")
            armed = store.arm(2281)
            calls = []

            async def fake_notify(**kwargs):
                calls.append(kwargs)

            with patch(
                "sber_screening_web_worker._notify",
                side_effect=fake_notify,
            ), patch(
                "sber_screening_web_worker.generate_suggestion"
            ) as suggestion:
                result = await _handle_new_message(
                    web=_FakeTerminalWeb(),
                    store=store,
                    bot_token="x",
                    chat_id=1,
                    last_unarmed_id=None,
                )

            self.assertEqual(result, 777)
            self.assertIsNone(store.get_active_session())
            latest = store.get_latest_session()
            self.assertEqual(latest["id"], armed["id"])
            self.assertEqual(latest["status"], "completed")
            history = store.history(int(armed["id"]))
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["status"], "terminal")
            self.assertIsNotNone(history[0]["notified_at"])
            self.assertEqual(len(calls), 1)
            suggestion.assert_not_called()

            with patch(
                "sber_screening_web_worker._notify",
                side_effect=fake_notify,
            ):
                await _handle_new_message(
                    web=_FakeTerminalWeb(),
                    store=store,
                    bot_token="x",
                    chat_id=1,
                    last_unarmed_id=777,
                )
            self.assertEqual(len(calls), 1)


class SberScreeningAutoStartTest(unittest.IsolatedAsyncioTestCase):
    async def test_giga_greeting_auto_starts_after_completed_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SberScreeningStore(Path(temp_dir) / "sber.sqlite3")
            old = store.arm(2191)
            store.complete_session(int(old["id"]))
            calls = []

            async def fake_notify(**kwargs):
                calls.append(kwargs)

            suggestion = type(
                "Suggestion",
                (),
                {
                    "answer": "Да, удобно.",
                    "confidence": "high",
                    "reason": "grounded",
                },
            )()

            with patch(
                "sber_screening_web_worker.resolve_application_for_vacancy_title",
                return_value=3026,
            ), patch(
                "sber_screening_web_worker.generate_suggestion",
                return_value=suggestion,
            ), patch(
                "sber_screening_web_worker._notify",
                side_effect=fake_notify,
            ):
                await _handle_new_message(
                    web=_FakeGreetingWeb(),
                    store=store,
                    bot_token="x",
                    chat_id=1,
                    last_unarmed_id=None,
                )

            active = store.get_active_session()
            self.assertIsNotNone(active)
            self.assertEqual(active["application_id"], 3026)
            turns = store.pending_turns(int(active["id"]))
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["external_message_id"], 888)
            self.assertEqual(turns[0]["suggested_answer"], "Да, удобно.")
            self.assertIsNotNone(turns[0]["notified_at"])
            self.assertEqual(len(calls), 1)


    async def test_external_giga_recovers_missed_greeting_without_application(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SberScreeningStore(Path(temp_dir) / "sber.sqlite3")
            old = store.arm(2191)
            old_turn = store.create_turn(
                session_id=int(old["id"]),
                external_message_id=900,
                question=(
                    "Спасибо за интервью! Я передам ваше резюме и итоги "
                    "нашего диалога рекрутеру."
                ),
            )
            store.mark_terminal(int(old_turn["id"]))
            store.complete_session(int(old["id"]))
            calls = []

            async def fake_notify(**kwargs):
                calls.append(kwargs)

            suggestion = type(
                "Suggestion",
                (),
                {
                    "answer": "Сейчас открыт к новым предложениям.",
                    "confidence": "medium",
                    "reason": "grounded",
                },
            )()

            with patch(
                "sber_screening_web_worker.resolve_application_for_vacancy_title",
                return_value=None,
            ), patch(
                "sber_screening_web_worker.generate_suggestion",
                return_value=suggestion,
            ) as generate, patch(
                "sber_screening_web_worker._notify",
                side_effect=fake_notify,
            ):
                await _handle_new_message(
                    web=_FakeMissedExternalGreetingWeb(),
                    store=store,
                    bot_token="x",
                    chat_id=1,
                    last_unarmed_id=None,
                )

            active = store.get_active_session()
            self.assertIsNotNone(active)
            self.assertEqual(active["application_id"], 0)
            self.assertEqual(
                active["vacancy_title"],
                "Руководитель продукта (GigaCode)",
            )
            turns = store.pending_turns(int(active["id"]))
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["external_message_id"], 902)
            self.assertEqual(
                turns[0]["suggested_answer"],
                "Сейчас открыт к новым предложениям.",
            )
            generate.assert_called_once()
            self.assertEqual(
                generate.call_args.kwargs["vacancy_title"],
                "Руководитель продукта (GigaCode)",
            )
            self.assertEqual(len(calls), 1)
            self.assertIn("External Giga screening", calls[0]["text"])

class SberScreeningNotificationDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_pending_turn_notifies_only_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SberScreeningStore(Path(temp_dir) / "sber.sqlite3")
            store.arm(2281)
            calls = []

            async def fake_notify(**kwargs):
                calls.append(kwargs)

            with patch(
                "sber_screening_web_worker._notify",
                side_effect=fake_notify,
            ):
                for _ in range(2):
                    await _handle_new_message(
                        web=_FakeChoiceWeb(),
                        store=store,
                        bot_token="x",
                        chat_id=1,
                        last_unarmed_id=None,
                    )

            self.assertEqual(len(calls), 1)
            session = store.get_active_session()
            turns = store.pending_turns(int(session["id"]))
            self.assertEqual(len(turns), 1)
            self.assertIsNotNone(turns[0]["notified_at"])


if __name__ == "__main__":
    unittest.main()
