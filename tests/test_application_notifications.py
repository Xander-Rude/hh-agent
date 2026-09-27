import os
import unittest
from unittest.mock import patch

from application_notifications import (
    build_cover_letter_attention_message,
    build_manual_required_message,
    notify_cover_letter_attention,
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
            cover_letter="Опыт <AI> & B2B",
        )

        self.assertIn("PM &lt;B2B&gt;", message)
        self.assertIn("A &amp; B", message)
        self.assertIn("Не найдено &lt;подтверждение&gt;", message)
        self.assertNotIn("Сопроводительное письмо", message)
        self.assertNotIn("Опыт &lt;AI&gt; &amp; B2B", message)

    def test_cover_letter_attention_does_not_claim_apply_failed(self) -> None:
        message = build_cover_letter_attention_message(
            vacancy_title="Project Manager",
            company="Example",
            application_id=2063,
            reason="HH не подтвердил письмо.",
            cover_letter="Письмо для Example",
        )

        self.assertIn("Отклик отправлен", message)
        self.assertIn("Повторно откликаться не нужно", message)
        self.assertIn("письмо требует внимания", message.lower())
        self.assertNotIn("Письмо для Example", message)
        self.assertNotIn("отклик не считается отправленным", message.lower())

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
            cover_letter="Готовое сопроводительное для Outlines",
            post=fake_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        self.assertEqual(len(calls), 2)

        status_payload = calls[0][1]
        self.assertNotIn("Готовое сопроводительное для Outlines", status_payload["text"])
        button = status_payload["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["text"], "Откликнуться вручную")
        self.assertEqual(button["url"], "https://hh.ru/vacancy/136656272")

        letter_payload = calls[1][1]
        self.assertEqual(
            letter_payload["text"],
            "Готовое сопроводительное для Outlines",
        )
        self.assertNotIn("parse_mode", letter_payload)
        self.assertNotIn("reply_markup", letter_payload)

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
        clear=False,
    )
    def test_cover_letter_attention_sends_letter_as_second_plain_message(self) -> None:
        calls = []

        def fake_post(url, *, json, timeout):
            calls.append((url, json, timeout))
            return FakeResponse()

        sent = notify_cover_letter_attention(
            vacancy_title="Project Manager",
            company="Example",
            vacancy_url="https://hh.ru/vacancy/2",
            application_id=2063,
            reason="HH не подтвердил письмо.",
            cover_letter="Строка 1\n\nСтрока 2 <без HTML>",
            post=fake_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("Строка 1", calls[0][1]["text"])
        self.assertEqual(
            calls[1][1]["text"],
            "Строка 1\n\nСтрока 2 <без HTML>",
        )
        self.assertNotIn("parse_mode", calls[1][1])

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
