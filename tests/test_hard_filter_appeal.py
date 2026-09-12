import unittest
from unittest.mock import patch

from app.hard_filter_appeal import (
    HardFilterAppealDecision,
    HardFilterAppealReviewer,
    appeal_confidence_threshold,
    should_override_hard_reject,
)


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def chat(self, messages, format_schema=None):
        self.calls.append(
            {
                "messages": messages,
                "format_schema": format_schema,
            }
        )
        return {
            "message": {
                "content": self.content,
            }
        }


class HardFilterAppealTests(unittest.TestCase):
    def test_override_requires_confidence_threshold(self):
        decision = HardFilterAppealDecision(
            verdict="override_reject",
            confidence=0.82,
            reason="Роль релевантна.",
        )
        self.assertTrue(
            should_override_hard_reject(
                decision,
                threshold=0.75,
            )
        )
        self.assertFalse(
            should_override_hard_reject(
                decision,
                threshold=0.90,
            )
        )

    def test_confirm_reject_never_overrides(self):
        decision = HardFilterAppealDecision(
            verdict="confirm_reject",
            confidence=0.99,
            reason="Фильтр прав.",
        )
        self.assertFalse(
            should_override_hard_reject(
                decision,
                threshold=0.75,
            )
        )

    def test_threshold_reads_environment(self):
        with patch.dict(
            "os.environ",
            {"HARD_FILTER_APPEAL_CONFIDENCE": "0.81"},
        ):
            self.assertEqual(
                appeal_confidence_threshold(),
                0.81,
            )

    def test_reviewer_parses_structured_response(self):
        llm = FakeLLM(
            '{"verdict":"override_reject","confidence":0.91,'
            '"reason":"Нестандартный title, но обязанности релевантны."}'
        )
        reviewer = HardFilterAppealReviewer(llm=llm)

        result = reviewer.review(
            resume="PMO, Program Management, IT leadership",
            vacancy="Руководитель технологического развития",
            preferences={"unwanted_domains": ["gambling"]},
            hard_filter_reason="Название роли не соответствует профилю",
            hard_filter_code="role_title",
        )

        self.assertEqual(result.verdict, "override_reject")
        self.assertEqual(result.confidence, 0.91)
        self.assertEqual(len(llm.calls), 1)
        prompt = llm.calls[0]["messages"][0]["content"]
        self.assertIn("Название роли не соответствует профилю", prompt)
        self.assertIn("Руководитель технологического развития", prompt)


if __name__ == "__main__":
    unittest.main()
