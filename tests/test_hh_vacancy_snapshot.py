from __future__ import annotations

import base64
import gzip
import json
import unittest
from datetime import UTC, datetime

from app.hh_vacancy_snapshot import (
    build_hh_source_payload,
    extract_key_skills,
    extract_published_at,
    extract_salary_fields,
    source_payload_content_hash,
)


class HHVacancySnapshotTests(unittest.TestCase):
    def test_build_payload_preserves_dom_and_optional_api_slot(self) -> None:
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

    def test_key_skills_use_exact_dom_skill_tags(self) -> None:
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
                        "data_qa": "vacancy-label-skillsPercentage",
                        "text": "Подходит по навыкам на 66%",
                    },
                    {
                        "data_qa": "vacancy-title",
                        "text": "Руководитель проектов",
                    },
                ]
            },
            api_error="not_requested",
        )

        self.assertEqual(
            extract_key_skills(payload),
            ["Проектная документация"],
        )

    def test_api_key_skills_remain_supported_if_payload_is_ever_supplied(self) -> None:
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

    def test_published_at_and_salary_helpers_stay_backward_compatible(self) -> None:
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
        first = build_hh_source_payload(
            hh_id="123",
            api_payload=None,
            dom_snapshot={
                "main_text": "same",
                "main_html_gzip_b64": "abc",
            },
            collected_at=datetime(2026, 9, 25, 0, 0),
        )
        second = build_hh_source_payload(
            hh_id="123",
            api_payload=None,
            dom_snapshot={
                "main_text": "same",
                "main_html_gzip_b64": "abc",
            },
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

    def test_compressed_html_round_trip_fixture(self) -> None:
        html = "<main><div data-qa=\"skills-element\">Roadmap</div></main>"
        encoded = base64.b64encode(
            gzip.compress(html.encode("utf-8"))
        ).decode("ascii")

        restored = gzip.decompress(
            base64.b64decode(encoded)
        ).decode("utf-8")

        self.assertEqual(restored, html)

    def test_payload_is_json_serializable_without_field_loss(self) -> None:
        payload = build_hh_source_payload(
            hh_id="123",
            api_payload=None,
            dom_snapshot={
                "main_text": "card",
                "meta": [
                    {
                        "property": "og:title",
                        "content": "Руководитель проектов",
                    }
                ],
                "json_ld": ['{"@type":"JobPosting"}'],
            },
        )

        restored = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        self.assertEqual(
            restored["dom"]["meta"][0]["content"],
            "Руководитель проектов",
        )
        self.assertEqual(
            restored["dom"]["json_ld"][0],
            '{"@type":"JobPosting"}',
        )


if __name__ == "__main__":
    unittest.main()
