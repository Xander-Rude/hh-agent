from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import hh_response_audit as audit


class ReadOnlyGuardTests(unittest.TestCase):
    def test_all_mutating_methods_are_blocked(self) -> None:
        url = "https://hh.ru/applicant/negotiations"
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertTrue(
                    audit.ReadOnlyRequestGuard.should_block(method, url)
                )

    def test_safe_negotiations_get_is_allowed(self) -> None:
        self.assertFalse(
            audit.ReadOnlyRequestGuard.should_block(
                "GET",
                "https://hh.ru/applicant/negotiations?filter=all&page=0",
            )
        )

    def test_side_effect_gets_are_blocked(self) -> None:
        urls = (
            "https://hh.ru/chatik/api/mark_read?chatId=123",
            "https://hh.ru/chatik/api/notify_chat_opened?chatId=123",
            "https://hh.ru/chatik/api/get_or_create_bot_dialog",
            "https://api.hh.ru/negotiations/123/messages",
            "https://hh.ru/chatik/api/messages?chatId=123",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(
                    audit.ReadOnlyRequestGuard.should_block("GET", url)
                )

    def test_source_contains_no_browser_click_or_fill(self) -> None:
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".click(", source)
        self.assertNotIn(".fill(", source)
        self.assertNotIn(".press(", source)


class ParserTests(unittest.TestCase):
    def test_response_from_topic_extracts_core_fields(self) -> None:
        topic = {
            "topicId": "987654",
            "vacancy": {
                "id": "136627682",
                "name": "Руководитель кластера разработки",
                "alternate_url": "https://hh.ru/vacancy/136627682",
                "employer": {"name": "Компания"},
            },
            "state": {"id": "response", "name": "Резюме просмотрено"},
            "createdAt": "2026-09-18T10:00:00+03:00",
            "viewedByOpponent": True,
            "messagesCount": 3,
            "lastMessage": {"createdAt": "2026-09-18T11:00:00+03:00"},
        }

        record = audit.response_from_topic(topic)

        self.assertEqual(record["application_id"], "987654")
        self.assertEqual(record["vacancy_id"], "136627682")
        self.assertEqual(
            record["vacancy_title"],
            "Руководитель кластера разработки",
        )
        self.assertEqual(record["company"], "Компания")
        self.assertEqual(record["viewed_by_employer"], 1)
        self.assertEqual(record["messages_count"], 3)

    def test_response_from_topic_reads_nested_ssr_fields(self) -> None:
        topic = {
            "topicId": "555",
            "payload": {
                "vacancyInfo": {
                    "id": "137500000",
                    "name": "Delivery Manager",
                    "employer": {"name": "Nested Company"},
                },
                "negotiationState": "response",
                "responseDate": "2026-09-18T09:30:00+03:00",
                "viewedByOpponent": True,
                "negotiationUrl": "/applicant/negotiations/555",
            },
        }

        record = audit.response_from_topic(topic)

        self.assertEqual(record["vacancy_id"], "137500000")
        self.assertEqual(record["vacancy_title"], "Delivery Manager")
        self.assertEqual(record["company"], "Nested Company")
        self.assertEqual(record["applied_at"], "2026-09-18T09:30:00+03:00")
        self.assertEqual(
            record["chat_negotiation_url"],
            "https://hh.ru/applicant/negotiations/555",
        )
        self.assertEqual(record["viewed_by_employer"], 1)

    def test_event_derivation_builds_funnel_fields(self) -> None:
        record = {
            "application_id": "42",
            "current_status": "В работе",
        }
        events = [
            {
                "event_type": "resume_viewed",
                "timestamp": "2026-09-18T10:00:00+03:00",
            },
            {
                "event_type": "employer_message",
                "timestamp": "2026-09-18T12:00:00+03:00",
            },
            {
                "event_type": "employer_invite",
                "timestamp": "2026-09-18T13:00:00+03:00",
            },
        ]

        result = audit.derive_from_events(record, events)

        self.assertEqual(result["viewed_by_employer"], 1)
        self.assertEqual(
            result["viewed_at"],
            "2026-09-18T10:00:00+03:00",
        )
        self.assertEqual(result["employer_replied"], 1)
        self.assertEqual(
            result["first_reply_at"],
            "2026-09-18T12:00:00+03:00",
        )
        self.assertEqual(result["invited"], 1)
        self.assertEqual(result["active_dialog"], 1)


class StoreTests(unittest.TestCase):
    def test_response_and_event_upserts_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = audit.AuditStore(Path(temp_dir) / "audit.sqlite")
            try:
                record = {
                    "application_id": "100",
                    "negotiation_id": "100",
                    "vacancy_id": "200",
                    "vacancy_title": "Project Manager",
                    "company": "Example",
                    "current_status": "Резюме просмотрено",
                }
                store.upsert_response(record)
                store.upsert_response(record)

                responses = store.conn.execute(
                    "SELECT COUNT(*) FROM responses"
                ).fetchone()[0]
                self.assertEqual(responses, 1)

                event = {
                    "application_id": "100",
                    "timestamp": "2026-09-18T10:00:00+03:00",
                    "author": "employer",
                    "event_type": "resume_viewed",
                    "text": "Резюме просмотрено",
                }
                store.upsert_event(event)
                store.upsert_event(event)

                events = store.conn.execute(
                    "SELECT COUNT(*) FROM response_events"
                ).fetchone()[0]
                self.assertEqual(events, 1)
            finally:
                store.close()

    def test_checkpoint_queue_survives_resume_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = audit.AuditStore(Path(temp_dir) / "audit.sqlite")
            try:
                run_id = store.create_run(
                    mode="limit",
                    requested_limit=10,
                )
                store.enqueue(
                    run_id,
                    position=0,
                    application_id="100",
                    detail_url="https://hh.ru/applicant/negotiations/100",
                )
                store.enqueue(
                    run_id,
                    position=1,
                    application_id="100",
                    detail_url="https://hh.ru/applicant/negotiations/100",
                )

                self.assertEqual(store.queued_count(run_id), 1)
                store.mark_run_failed(
                    run_id,
                    RuntimeError("synthetic crash"),
                )

                resumed = store.resumable_run()
                self.assertIsNotNone(resumed)
                assert resumed is not None
                self.assertEqual(resumed["run_id"], run_id)

                pending = list(store.pending_queue(run_id))
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["application_id"], "100")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
