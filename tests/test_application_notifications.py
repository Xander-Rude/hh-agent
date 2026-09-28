import os
import unittest
from pathlib import Path
from unittest.mock import patch

from application_notifications import (
    build_cover_letter_attention_message,
    build_manual_required_message,
    notify_manual_required,
)


class FakeResponse:
    closed = False

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


ROOT = Path(__file__).resolve().parents[1]
APPLY_WORKER_SOURCE = (ROOT / "apply_worker.py").read_text(encoding="utf-8")


class ApplicationNotificationTests(unittest.TestCase):
    def test_apply_worker_passes_stored_cover_letter_to_manual_notification(self) -> None:
        self.assertIn(
            '"cover_letter": (\n'
            '                        (application.cover_letter or "").strip()',
            APPLY_WORKER_SOURCE,
        )

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

    def test_cover_letter_attention_does_not_claim_apply_failed(self) -> None:
        message = build_cover_letter_attention_message(
            vacancy_title="Project Manager",
            company="Example",
            application_id=2063,
            reason="HH не подтвердил письмо.",
        )

        self.assertIn("Отклик отправлен", message)
        self.assertIn("Повторно откликаться не нужно", message)
        self.assertIn("письмо требует внимания", message.lower())
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

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
        clear=False,
    )
    def test_manual_required_sends_cover_letter_as_followup_message(self) -> None:
        calls = []

        def fake_post(url, *, json, timeout):
            calls.append((url, json, timeout))
            return FakeResponse()

        letter = (
            "Hello!\n\n"
            "My experience is centered on end-to-end IT project delivery.\n\n"
            "Best regards,\nAleksandr Rudenko"
        )
        sent = notify_manual_required(
            vacancy_title="Project Manager",
            company="Example",
            vacancy_url="https://hh.ru/vacancy/2",
            application_id=2,
            reason="Manual completion required.",
            cover_letter=letter,
            post=fake_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        self.assertEqual(len(calls), 2)
        card = calls[0][1]
        followup = calls[1][1]
        self.assertIn("reply_markup", card)
        self.assertEqual(followup["text"], letter)
        self.assertNotIn("parse_mode", followup)
        self.assertNotIn("reply_markup", followup)
        self.assertTrue(followup["disable_web_page_preview"])

    @patch.dict(
        os.environ,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
        clear=False,
    )
    def test_cover_letter_retry_does_not_duplicate_manual_required_card(self) -> None:
        calls = []
        letter_attempts = 0

        def flaky_post(url, *, json, timeout):
            nonlocal letter_attempts
            calls.append(json)
            if "reply_markup" not in json:
                letter_attempts += 1
                if letter_attempts == 1:
                    raise RuntimeError("Bad Gateway")
            return FakeResponse()

        sent = notify_manual_required(
            vacancy_title="Project Manager",
            company="Example",
            vacancy_url="https://hh.ru/vacancy/3",
            application_id=3,
            reason="Manual completion required.",
            cover_letter="Copy-ready cover letter",
            post=flaky_post,
            sleep=lambda _: None,
        )

        self.assertTrue(sent)
        cards = [item for item in calls if "reply_markup" in item]
        letters = [item for item in calls if "reply_markup" not in item]
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(letters), 2)
        self.assertEqual(letters[-1]["text"], "Copy-ready cover letter")


if __name__ == "__main__":
    unittest.main()
