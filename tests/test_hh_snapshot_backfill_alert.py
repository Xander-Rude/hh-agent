from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import backfill_hh_vacancy_snapshots as backfill


class BackfillCaptchaTelegramAlertTests(unittest.TestCase):
    def test_notification_posts_to_telegram(self) -> None:
        response = Mock()
        response.raise_for_status = Mock()

        with (
            patch.object(backfill, "TELEGRAM_BOT_TOKEN", "token"),
            patch.object(backfill, "TELEGRAM_CHAT_ID", "chat"),
            patch.object(backfill.httpx, "post", return_value=response) as post,
        ):
            sent = backfill.notify_telegram(
                "HH показал CAPTCHA / anti-bot."
            )

        self.assertTrue(sent)
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["chat_id"], "chat")
        self.assertIn(
            "snapshot backfill остановлен",
            kwargs["json"]["text"],
        )
        self.assertIn(
            "CAPTCHA / anti-bot",
            kwargs["json"]["text"],
        )

    def test_missing_credentials_does_not_raise(self) -> None:
        with (
            patch.object(backfill, "TELEGRAM_BOT_TOKEN", ""),
            patch.object(backfill, "TELEGRAM_CHAT_ID", ""),
            patch.object(backfill.httpx, "post") as post,
        ):
            sent = backfill.notify_telegram("blocked")

        self.assertFalse(sent)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
