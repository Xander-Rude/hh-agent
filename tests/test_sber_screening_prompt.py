from __future__ import annotations

import unittest

from app.sber_screening import ScreeningContext, build_prompt


class SberScreeningPromptTest(unittest.TestCase):
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
