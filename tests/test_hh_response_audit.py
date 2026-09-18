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

    def test_chatik_topic_url_is_allowlisted_read_endpoint(self) -> None:
        self.assertEqual(
            audit.chatik_topic_url("5586630199"),
            "https://chatik.hh.ru/chatik/api/chat_data_by_topic?topicId=5586630199",
        )
        self.assertEqual(
            audit.chatik_chat_url("5632516861"),
            "https://chatik.hh.ru/chatik/api/chat_data?chatId=5632516861",
        )
        with self.assertRaises(ValueError):
            audit.chatik_topic_url("1&evil=1")
        with self.assertRaises(ValueError):
            audit.chatik_chat_url("1&evil=1")

    def test_captcha_detection_by_url_and_text(self) -> None:
        self.assertIsNotNone(
            audit.challenge_reason(
                "https://hh.ru/account/captcha",
                "",
                "",
            )
        )
        self.assertIsNotNone(
            audit.challenge_reason(
                "https://hh.ru/applicant/negotiations",
                "Проверка безопасности",
                "Подтвердите, что вы не робот",
            )
        )
        self.assertIsNone(
            audit.challenge_reason(
                "https://hh.ru/applicant/negotiations",
                "Отклики и приглашения",
                "Ваши отклики на вакансии",
            )
        )

    def test_source_contains_no_browser_click_or_fill(self) -> None:
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".click(", source)
        self.assertNotIn(".fill(", source)
        self.assertNotIn(".press(", source)
        self.assertNotIn("context.request.post(", source)
        self.assertNotIn("context.request.put(", source)
        self.assertNotIn("context.request.patch(", source)
        self.assertNotIn("context.request.delete(", source)


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
                "lastState": "INVITE",
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
        self.assertEqual(record["current_status"], "INVITE")
        self.assertEqual(record["invited"], 1)

    def test_chat_index_helpers_map_topic_and_activity(self) -> None:
        item = {
            "id": "9001",
            "unreadCount": 0,
            "lastMessage": {
                "id": "777",
                "text": "Добрый день",
                "createdAt": "2026-09-18T10:00:00+03:00",
            },
            "resources": {
                "NEGOTIATION_TOPIC": ["5586630199"],
                "VACANCY": ["136627682"],
            },
        }

        self.assertEqual(
            audit._chat_topic_ids(item),
            {"5586630199", "9001"},
        )
        self.assertEqual(
            audit._chat_negotiation_topic_ids(item),
            {"5586630199"},
        )
        self.assertEqual(
            audit._chat_vacancy_ids(item),
            {"136627682"},
        )
        self.assertTrue(audit._chat_item_has_activity(item))

    def test_chat_match_uses_canonical_negotiation_topic_for_chat_alias(self) -> None:
        entry = {
            "chat_id": "9001",
            "topic_id": "5586630199",
            "topic_ids": ["5586630199"],
            "vacancy_ids": ["136627682"],
            "has_activity": True,
        }
        index = {
            "9001": entry,
            "5586630199": entry,
        }

        matched = audit.chatik_match_entry(
            {
                "application_id": "9001",
                "negotiation_id": "9001",
                "vacancy_id": "136627682",
            },
            index,
        )

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched["topic_id"], "5586630199")

    def test_chat_match_can_fallback_to_unique_vacancy(self) -> None:
        entry = {
            "chat_id": "9001",
            "topic_id": "5586630199",
            "topic_ids": ["5586630199"],
            "vacancy_ids": ["136627682"],
            "has_activity": True,
        }

        matched = audit.chatik_match_entry(
            {
                "application_id": "some-other-id",
                "negotiation_id": "some-other-id",
                "vacancy_id": "136627682",
            },
            {"5586630199": entry, "9001": entry},
        )

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched["topic_id"], "5586630199")

    def test_chat_index_helper_treats_empty_chat_as_inactive(self) -> None:
        item = {
            "id": "9002",
            "unreadCount": 0,
            "lastMessage": {},
            "resources": {
                "NEGOTIATION_TOPIC": ["5586630200"],
            },
        }

        self.assertFalse(audit._chat_item_has_activity(item))

    def test_chatik_payload_builds_messages_and_rejection(self) -> None:
        payload = {
            "chat": {
                "currentParticipantId": "me",
                "messages": {
                    "items": [
                        {
                            "id": "1",
                            "type": "SIMPLE",
                            "participantId": "me",
                            "text": "Здравствуйте",
                            "createdAt": "2026-09-18T10:00:00+03:00",
                        },
                        {
                            "id": "2",
                            "type": "SIMPLE",
                            "participantId": "hr",
                            "text": "Добрый день",
                            "createdAt": "2026-09-18T11:00:00+03:00",
                        },
                        {
                            "id": "3",
                            "type": "SYSTEM",
                            "text": "Работодатель отказал",
                            "createdAt": "2026-09-18T12:00:00+03:00",
                            "workflowTransition": {"id": "DISCARD"},
                        },
                    ]
                },
            }
        }

        record, events = audit.enrich_from_chatik_payload(
            {
                "application_id": "42",
                "negotiation_id": "42",
            },
            payload,
        )

        event_types = [event["event_type"] for event in events]
        self.assertIn("candidate_message", event_types)
        self.assertIn("employer_message", event_types)
        self.assertIn("rejection", event_types)
        self.assertEqual(record["employer_replied"], 1)
        self.assertEqual(record["rejected"], 1)
        self.assertEqual(record["employer_replied"], 1)
        self.assertEqual(record["messages_count"], 2)
        self.assertEqual(
            record["first_reply_at"],
            "2026-09-18T11:00:00+03:00",
        )

    def test_chatik_participant_join_is_system_not_employer_message(self) -> None:
        payload = {
            "chat": {
                "currentParticipantId": "me",
                "messages": {
                    "items": [
                        {
                            "id": "1",
                            "type": "PARTICIPANT_JOINED",
                            "participantId": "hr",
                            "text": "",
                            "creationTime": "2026-09-18T10:00:00+03:00",
                        }
                    ]
                },
            }
        }

        events = audit.events_from_chatik_payload("42", payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["author"], "system")
        self.assertNotEqual(events[0]["event_type"], "employer_message")

    def test_chatik_workflow_applicant_state_becomes_application_event(self) -> None:
        payload = {
            "chat": {
                "currentParticipantId": "me",
                "messages": {
                    "items": [
                        {
                            "id": "1",
                            "type": "SIMPLE",
                            "participantId": "me",
                            "text": "",
                            "creationTime": "2026-09-18T10:00:00+03:00",
                            "workflowTransition": {
                                "id": 123,
                                "applicantState": "RESPONSE",
                            },
                        }
                    ]
                },
            }
        }

        events = audit.events_from_chatik_payload("42", payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["author"], "system")
        self.assertEqual(events[0]["event_type"], "application_submitted")

    def test_discard_status_counts_as_reply_without_chat(self) -> None:
        result = audit.derive_from_events(
            {
                "application_id": "42",
                "current_status": "DISCARD",
            },
            [],
        )

        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["employer_replied"], 1)
        self.assertEqual(result["active_dialog"], 0)

    def test_record_state_generates_view_and_rejection_events(self) -> None:
        record = {
            "application_id": "42",
            "current_status": "DISCARD",
            "viewed_by_employer": 1,
            "source_updated_at": "2026-09-18T12:30:00+03:00",
        }

        status_event = audit.status_event_from_record(record)
        viewed_event = audit.viewed_event_from_record(record)

        self.assertIsNotNone(status_event)
        assert status_event is not None
        self.assertEqual(status_event["event_type"], "rejection")
        self.assertEqual(status_event["author"], "employer")
        self.assertEqual(
            status_event["timestamp"],
            "2026-09-18T12:30:00+03:00",
        )
        self.assertIsNotNone(viewed_event)
        assert viewed_event is not None
        self.assertEqual(viewed_event["event_type"], "resume_viewed")

    def test_rejection_without_message_counts_as_employer_reply(self) -> None:
        record = {
            "application_id": "42",
            "current_status": "response",
        }
        events = [
            {
                "event_type": "rejection",
                "timestamp": "2026-09-18T12:30:00+03:00",
            }
        ]

        result = audit.derive_from_events(record, events)

        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["employer_replied"], 1)
        self.assertEqual(
            result["first_reply_at"],
            "2026-09-18T12:30:00+03:00",
        )
        self.assertEqual(result["active_dialog"], 0)

    def test_application_event_sets_applied_at(self) -> None:
        record = {"application_id": "42"}
        result = audit.derive_from_events(
            record,
            [
                {
                    "event_type": "application_submitted",
                    "timestamp": "2026-09-18T09:00:00+03:00",
                }
            ],
        )
        self.assertEqual(
            result["applied_at"],
            "2026-09-18T09:00:00+03:00",
        )

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
