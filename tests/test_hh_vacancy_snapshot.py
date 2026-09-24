from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from app.hh_vacancy_snapshot import (
    build_hh_source_payload,
    extract_key_skills,
    extract_published_at,
    extract_salary_fields,
    fetch_hh_vacancy_api_payload,
    source_payload_content_hash,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def json(self):
        return self.payload


class HHVacancySnapshotTests(unittest.TestCase):
    def test_fetch_keeps_complete_api_payload(self) -> None:
        expected = {
            "id": "123",
            "name": "Руководитель проектов",
            "key_skills": [{"name": "Управление проектами"}],
            "future_unknown_field": {
                "nested": [1, 2, 3],
            },
        }
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return FakeResponse(expected)

        result = fetch_hh_vacancy_api_payload(
            "123",
            request_get=fake_get,
        )

        self.assertEqual(result, expected)
        self.assertEqual(
            seen["url"],
            "https://api.hh.ru/vacancies/123",
        )
        self.assertIn("User-Agent", seen["kwargs"]["headers"])

    def test_build_payload_preserves_unknown_fields_and_dom(self) -> None:
        api = {
            "id": "123",
            "future_unknown_field": {"answer": 42},
        }
        dom = {
            "main_text": "Visible vacancy card",
            "data_qa": [
                {
                    "data_qa": "skills-element",
                    "text": "Управление проектами",
                    "href": None,
                }
            ],
        }
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload=api,
            dom_snapshot=dom,
            collected_at=datetime(
                2026,
                9,
                25,
                0,
                0,
                tzinfo=UTC,
            ),
        )

        self.assertEqual(
            payload["api"]["future_unknown_field"]["answer"],
            42,
        )
        self.assertEqual(
            payload["dom"]["main_text"],
            "Visible vacancy card",
        )

    def test_key_skills_prefer_structured_api_tags(self) -> None:
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload={
                "key_skills": [
                    {"name": "Управление проектами"},
                    {"name": "Stakeholder Management"},
                    {"name": "Управление проектами"},
                ]
            },
            dom_snapshot={
                "data_qa": [
                    {
                        "data_qa": "skills-element",
                        "text": "DOM fallback",
                    }
                ]
            },
        )

        self.assertEqual(
            extract_key_skills(payload),
            [
                "Управление проектами",
                "Stakeholder Management",
            ],
        )

    def test_key_skills_fall_back_to_dom(self) -> None:
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload=None,
            dom_snapshot={
                "data_qa": [
                    {
                        "data_qa": "skills-element",
                        "text": "Проектная документация",
                    },
                    {
                        "data_qa": "vacancy-title",
                        "text": "Руководитель проектов",
                    },
                ]
            },
            api_error="temporary error",
        )

        self.assertEqual(
            extract_key_skills(payload),
            ["Проектная документация"],
        )

    def test_published_at_and_salary_are_normalized_from_api(self) -> None:
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload={
                "published_at": "2026-09-24T18:30:00+0300",
                "salary": {
                    "from": 300000,
                    "to": 400000,
                    "currency": "RUR",
                    "gross": False,
                },
            },
            dom_snapshot={},
        )

        self.assertEqual(
            extract_published_at(payload),
            datetime(2026, 9, 24, 15, 30),
        )
        self.assertEqual(
            extract_salary_fields(payload),
            (300000, 400000, "RUR"),
        )

    def test_hash_ignores_collection_timestamp(self) -> None:
        base = {
            "id": "123",
            "key_skills": [{"name": "Управление проектами"}],
        }
        first = build_hh_source_payload(
            hh_id="123",
            api_payload=base,
            dom_snapshot={"main_text": "same"},
            collected_at=datetime(2026, 9, 25, 0, 0),
        )
        second = build_hh_source_payload(
            hh_id="123",
            api_payload=base,
            dom_snapshot={"main_text": "same"},
            collected_at=datetime(2026, 9, 25, 1, 0),
        )

        self.assertNotEqual(
            first["collected_at"],
            second["collected_at"],
        )
        self.assertEqual(
            source_payload_content_hash(first),
            source_payload_content_hash(second),
        )

    def test_payload_is_json_serializable_without_field_loss(self) -> None:
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload={
                "key_skills": [{"name": "Roadmap"}],
                "employer": {"id": "7", "name": "Example"},
                "address": {"city": "Москва"},
            },
            dom_snapshot={"main_text": "card"},
        )

        restored = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        self.assertEqual(
            restored["api"]["employer"]["name"],
            "Example",
        )
        self.assertEqual(
            restored["api"]["address"]["city"],
            "Москва",
        )


if __name__ == "__main__":
    unittest.main()
