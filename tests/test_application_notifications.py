import os
import unittest
from unittest.mock import patch

from application_notifications import (
    build_manual_required_message,
    notify_manual_required,
)


class FakeResponse:
    closed = False

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class ApplicationNotificationTests(unittest.TestCase):
    def test_message_escapes_dynamic_html(self) -> None:
        message = build_manual_required_message(
            vacancy_title="PM <B2B>",
            company="A & B",
            application_id=366,
            reason="Не найдено <подтверждение>",
        )

        self.assertIn("PM &lt;B2B&gt;", message)
        self.assertIn("A &amp; B", message)
        self.assertIn("Не найдено &lt;подтверждение&gt;", message)

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
        clear=False,
    )
    def test_notification_contains_manual_apply_button(self) -> None:
        calls = []

        def fake_post(url, *, json, timeout):
            calls.append((url, json, timeout))
            return FakeResponse()

        sent = notify_manual_required(
            vacancy_title="Менеджер продукта",
            company="Outlines",
            vacancy_url="https://hh.ru/vacancy/136656272",
            application_id=366,
            reason="Подтверждение успешного отклика не найдено.",
            post=fake_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        self.assertEqual(len(calls), 1)
        payload = calls[0][1]
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["text"], "Откликнуться вручную")
        self.assertEqual(button["url"], "https://hh.ru/vacancy/136656272")

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
        clear=False,
    )
    def test_notification_retries_network_failure(self) -> None:
        attempts = []

        def flaky_post(url, *, json, timeout):
            attempts.append(url)
            if len(attempts) < 3:
                raise RuntimeError("Bad Gateway")
            return FakeResponse()

        sent = notify_manual_required(
            vacancy_title="PM",
            company=None,
            vacancy_url="https://hh.ru/vacancy/1",
            application_id=1,
            reason="Нужна ручная проверка.",
            post=flaky_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        self.assertEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
