import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import telegram_bot


class TelegramStatusUxTests(unittest.TestCase):
    def test_status_hides_lock_internals_and_shows_human_state(self) -> None:
        accounts = [
            SimpleNamespace(key="old", label="⚪ OLD"),
            SimpleNamespace(key="clean", label="🟢 CLEAN"),
        ]
        queue = {
            "unprocessed": 27,
            "notified": 2,
            "approved": 0,
            "applying": 0,
            "manual_required": 0,
            "hh_by_account": {
                "old": {"notified": 0},
                "clean": {"notified": 2},
            },
        }

        with patch.object(telegram_bot, "all_accounts", return_value=accounts):
            lines = telegram_bot._build_status_lines(
                pipeline_state={
                    "status": "running",
                    "stage": "clean_shadow",
                },
                apply_state={
                    "status": "skipped",
                    "stage": "global_lock",
                    "last_error": "agent_lock_busy",
                },
                resume_raise_state={
                    "status": "skipped",
                    "stage": "global_lock",
                    "last_error": "agent_lock_busy",
                },
                queue=queue,
            )

        text = "\n".join(lines)
        self.assertIn("проверяет CLEAN-кандидатов", text)
        self.assertIn("Не обработано: 27", text)
        self.assertIn("Ждут решения: 2", text)
        self.assertIn("🟢 CLEAN 2", text)
        self.assertIn("⚪ OLD 0", text)
        self.assertIn("Отклики: очередь пуста", text)
        self.assertIn("ждёт свободного слота", text)
        self.assertIn("/tech", text)
        self.assertNotIn("agent_lock_busy", text)
        self.assertNotIn("global_lock", text)
        self.assertNotIn("heartbeat", text)

    def test_disabled_sources_do_not_pollute_operator_counters(self) -> None:
        counts = telegram_bot._operator_status_counts(
            [
                ("hh", "manual_required", 2),
                ("ozon", "manual_required", 18),
                ("tbank", "manual_required", 3),
                ("vk", "notified", 4),
            ]
        )

        self.assertEqual(counts.get("manual_required"), 2)
        self.assertEqual(counts.get("notified"), 4)
        self.assertNotIn("ozon", counts)
        self.assertNotIn("tbank", counts)

    def test_status_surfaces_real_worker_failure(self) -> None:
        accounts = [SimpleNamespace(key="clean", label="🟢 CLEAN")]
        queue = {
            "unprocessed": 0,
            "notified": 0,
            "approved": 1,
            "applying": 0,
            "manual_required": 0,
            "hh_by_account": {"clean": {"notified": 0}},
        }

        with patch.object(telegram_bot, "all_accounts", return_value=accounts):
            lines = telegram_bot._build_status_lines(
                pipeline_state={"status": "ok", "stage": "done"},
                apply_state={
                    "status": "failed",
                    "stage": "apply_dispatcher",
                    "last_error": "boom",
                },
                resume_raise_state={"status": "ok", "stage": "done"},
                queue=queue,
            )

        text = "\n".join(lines)
        self.assertIn("Apply worker: ошибка", text)
        self.assertIn("ждут 1", text)

    def test_unknown_pipeline_stage_is_still_readable(self) -> None:
        line = telegram_bot._human_pipeline_line(
            {"status": "running", "stage": "some_new_stage"}
        )
        self.assertEqual(line, "🔄 Сейчас: some new stage")


if __name__ == "__main__":
    unittest.main()
