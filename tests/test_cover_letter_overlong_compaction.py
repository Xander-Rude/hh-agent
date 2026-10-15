from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.canonical_cover_letter import (
    CoverLetterValidationError,
    _strip_signature,
    _trim_overlong_letter_at_sentence,
    cover_letter_guard_issues,
    generate_cover_letter_text,
)


class _TooLongProvider:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def chat(self, *, messages):
        self.calls += 1
        return SimpleNamespace(message=SimpleNamespace(content=self.text))


class CoverLetterSafeCompactionTests(unittest.TestCase):
    def test_only_complete_sentences_removed_and_guards_rechecked(self):
        body = (
            "Здравствуйте! Я координировал разработку и внедрение IT-систем. "
            + (
                "Я организовывал работу аналитиков, инженеров и тестирования, "
                "контролировал риски, сроки и результат внедрения. "
            ) * 28
        )
        llm = _TooLongProvider(body)
        result = generate_cover_letter_text(
            vacancy_title="Менеджер проектов",
            vacancy_company="Пример",
            vacancy_description="Нужно управлять технологическими проектами",
            resume_text="Управлял IT-проектами, инженерными командами и внедрениями.",
            preferences={},
            llm=llm,
        )
        self.assertEqual(llm.calls, 2)
        actual_body = _strip_signature(result.final_text)
        self.assertTrue(350 <= len(actual_body) <= 1400)
        self.assertTrue(actual_body.endswith("."))
        self.assertTrue(body.startswith(actual_body))
        self.assertEqual(
            cover_letter_guard_issues(
                result.final_text,
                vacancy_title="Менеджер проектов",
                vacancy_company="Пример",
                resume_text="Управлял IT-проектами, инженерными командами и внедрениями.",
            ),
            [],
        )

    def test_does_not_cut_a_sentence_in_half(self):
        body = "Здравствуйте! Я " + ("координировал " * 130) + "проекты."
        text = body + "\n\nС уважением,\nАлександр Руденко"
        self.assertEqual(_trim_overlong_letter_at_sentence(text), text)

    def test_no_valid_sentence_boundary_keeps_validation_fail_closed(self):
        body = "Здравствуйте! Я " + ("координировал " * 130) + "проекты."
        llm = _TooLongProvider(body)
        with self.assertRaisesRegex(CoverLetterValidationError, "too_long"):
            generate_cover_letter_text(
                vacancy_title="Менеджер проектов",
                vacancy_company="Пример",
                vacancy_description="ИТ-проекты",
                resume_text="Координировал проекты.",
                preferences={},
                llm=llm,
            )


if __name__ == "__main__":
    unittest.main()
