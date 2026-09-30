from __future__ import annotations

import unittest
from unittest.mock import patch

from app.sber_screening import ScreeningContext, build_prompt, generate_suggestion


class SberScreeningPromptTest(unittest.TestCase):
    def test_general_job_search_motivation_falls_back_to_grounded_answer(self):
        context = ScreeningContext(
            application_id=2016,
            vacancy_title="Руководитель проектов (Маркетплейс КАСКО)",
            company="Сбер",
            vacancy_description="Управление IT-проектами и delivery.",
            selected_resume_title="Руководитель проектов",
            evaluation_strengths=[],
            verified_facts=[
                "13+ лет опыта в IT.",
                "Есть опыт управления кросс-функциональными IT-командами.",
                "Есть опыт полного цикла delivery: от требований и планирования до production и эксплуатации.",
            ],
            resume_text="",
        )

        class RefusingLLM:
            def chat(self, **kwargs):
                return {
                    "message": {
                        "content": (
                            '{"mode":"needs_user","answer":"","confidence":"needs_user",'
                            '"reason":"Нет личных мотивов."}'
                        )
                    }
                }

        with patch("app.sber_screening.load_context", return_value=context):
            result = generate_suggestion(
                application_id=2016,
                question="Почему сейчас рассматриваете предложения о работе?",
                history=[],
                llm=RefusingLLM(),
            )

        self.assertEqual(result.confidence, "medium")
        self.assertTrue(result.answer)
        self.assertIn("кросс-функциональными командами", result.answer)
        self.assertIn("Маркетплейс КАСКО", result.answer)
        self.assertNotIn("увол", result.answer.lower())

    def test_prompt_prefers_bounded_answer_over_false_needs_user(self):
        context = ScreeningContext(
            application_id=1,
            vacancy_title="Delivery Manager",
            company="Сбер",
            vacancy_description="QA, CI/CD, delivery",
            selected_resume_title="Руководитель проектов",
            evaluation_strengths=["Управление QA и CI/CD."],
            verified_facts=["Есть опыт управления QA и автоматизированным тестированием."],
            resume_text="Развивал CI/CD и автоматизированное тестирование.",
        )
        prompt = build_prompt(
            context,
            (
                "Как выстраиваете пирамиду тестов: unit/integration/E2E, "
                "flakiness и контрактные тесты?"
            ),
            [],
        )
        self.assertIn("Предпочитай честный ограниченный ответ", prompt)
        self.assertIn(
            "само по себе НЕ является причиной для needs_user",
            prompt,
        )
        self.assertIn(
            "Используй needs_user только когда без нового факта",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
