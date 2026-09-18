import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.evaluator import AI_PROJECT_URL, VacancyEvaluator


RESUME = (
    "Program Management, PMO, управление портфелем 30+ IT-проектов, "
    "управление командами и C-level stakeholders."
)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Unexpected extra LLM call")
        return SimpleNamespace(
            message=SimpleNamespace(
                content=self.responses.pop(0),
            )
        )


def evaluation_json(*, ai_relevant: bool) -> str:
    return json.dumps(
        {
            "score": 90,
            "decision": "apply",
            "role_match": 90,
            "seniority_match": 90,
            "domain_match": 90,
            "responsibility_match": 90,
            "must_have_missing": [],
            "nice_to_have_missing": [],
            "strengths": [
                "Управление портфелем IT-проектов и командами."
            ],
            "gaps": [],
            "red_flags": [],
            "summary": "Высокое соответствие управленческому профилю.",
            "recommendation": "Можно откликаться.",
            "ai_relevant": ai_relevant,
            "cover_letter": (
                "Здравствуйте!\n\n"
                "Управляю крупными IT-проектами и портфелями, координирую "
                "команды и работаю с ключевыми стейкхолдерами. Этот опыт "
                "релевантен задачам позиции."
            ),
        },
        ensure_ascii=False,
    )


class AiRelevantCoverLetterTests(unittest.TestCase):
    def test_ai_relevant_vacancy_allows_project_context(self):
        llm = FakeLLM(
            [
                evaluation_json(ai_relevant=True),
                (
                    "Здравствуйте!\n\n"
                    "Управляю крупными IT-проектами и командами, а также "
                    "развиваю собственный AI-agent для автоматизации полного "
                    "workflow работы с вакансиями: "
                    f"{AI_PROJECT_URL}. Это дает мне практический контекст "
                    "для задач по внедрению AI-продуктов."
                ),
            ]
        )
        evaluator = VacancyEvaluator(llm=llm)

        with patch.dict(
            os.environ,
            {"HH_ENABLE_AI_PROJECT_COVER_LETTER": "true"},
        ):
            result = evaluator.evaluate(
                resume=RESUME,
                vacancy=(
                    "Название: Руководитель AI-продуктов\n"
                    "Описание: отвечать за внедрение LLM/RAG решений, управлять "
                    "AI-продуктами и командами, формировать roadmap GenAI."
                ),
                preferences={},
            )

        self.assertTrue(result.ai_relevant)
        self.assertEqual(len(llm.calls), 2)

        first_prompt = llm.calls[0]["messages"][0]["content"]
        second_prompt = llm.calls[1]["messages"][0]["content"]

        self.assertNotIn(AI_PROJECT_URL, first_prompt)
        self.assertIn("ai_relevant", first_prompt)
        self.assertIn(AI_PROJECT_URL, second_prompt)
        self.assertIn(AI_PROJECT_URL, result.cover_letter)
        self.assertIn("Александр Руденко", result.cover_letter)

    def test_ai_relevant_vacancy_forces_site_if_enrichment_drops_it(self):
        llm = FakeLLM(
            [
                evaluation_json(ai_relevant=True),
                (
                    "Здравствуйте!\n\n"
                    "Управляю крупными IT-проектами, программами и delivery, "
                    "работаю с командами и ключевыми стейкхолдерами. "
                    "Практический опыт с AI и LLM использую в рабочих задачах."
                ),
            ]
        )
        evaluator = VacancyEvaluator(llm=llm)

        with patch.dict(
            os.environ,
            {"HH_ENABLE_AI_PROJECT_COVER_LETTER": "true"},
        ):
            result = evaluator.evaluate(
                resume=RESUME,
                vacancy=(
                    "Название: Руководитель проекта GenAI\n"
                    "Описание: внедрение LLM и GenAI, управление AI-продуктами, "
                    "roadmap и кросс-функциональной командой."
                ),
                preferences={},
            )

        self.assertTrue(result.ai_relevant)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn(AI_PROJECT_URL, result.cover_letter)
        self.assertLess(
            result.cover_letter.index(AI_PROJECT_URL),
            result.cover_letter.index("С уважением"),
        )

    def test_ai_relevant_vacancy_keeps_site_if_enrichment_fails(self):
        llm = FakeLLM(
            [evaluation_json(ai_relevant=True)]
        )
        evaluator = VacancyEvaluator(llm=llm)

        with patch.dict(
            os.environ,
            {"HH_ENABLE_AI_PROJECT_COVER_LETTER": "true"},
        ):
            result = evaluator.evaluate(
                resume=RESUME,
                vacancy=(
                    "Название: Руководитель проекта GenAI\n"
                    "Описание: внедрение LLM и GenAI, управление AI-продуктами."
                ),
                preferences={},
            )

        self.assertTrue(result.ai_relevant)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn(AI_PROJECT_URL, result.cover_letter)

    def test_ai_relevant_vacancy_does_not_receive_project_context_by_default(self):
        llm = FakeLLM([evaluation_json(ai_relevant=True)])
        evaluator = VacancyEvaluator(llm=llm)

        with patch.dict(
            os.environ,
            {"HH_ENABLE_AI_PROJECT_COVER_LETTER": "false"},
        ):
            result = evaluator.evaluate(
                resume=RESUME,
                vacancy=(
                    "Название: Руководитель проекта GenAI\n"
                    "Описание: внедрение LLM и GenAI, управление AI-продуктами."
                ),
                preferences={},
            )

        self.assertTrue(result.ai_relevant)
        self.assertEqual(len(llm.calls), 1)
        self.assertNotIn(AI_PROJECT_URL, result.cover_letter)

        prompt = llm.calls[0]["messages"][0]["content"]
        self.assertIn("ЦЕЛЕВОЕ ПОЗИЦИОНИРОВАНИЕ", prompt)
        self.assertIn("Руководитель проектов", prompt)
        self.assertIn("НЕ упоминай личный AI-agent", prompt)
        self.assertIn("Head of PMO", prompt)
        self.assertIn("CTO", prompt)
        self.assertIn("не маскируй gaps", prompt)
        self.assertIn("1-2 наиболее релевантных", prompt)
        self.assertIn("максимум", prompt)
        self.assertIn("один такой масштабный факт", prompt)

    def test_regular_vacancy_never_receives_project_context(self):
        llm = FakeLLM(
            [evaluation_json(ai_relevant=False)]
        )
        evaluator = VacancyEvaluator(llm=llm)

        result = evaluator.evaluate(
            resume=RESUME,
            vacancy=(
                "Название: Senior Project Manager\n"
                "Описание: управление IT-проектами, бюджетами, сроками, "
                "рисками и кросс-функциональными командами."
            ),
            preferences={},
        )

        self.assertFalse(result.ai_relevant)
        self.assertEqual(len(llm.calls), 1)

        prompt = llm.calls[0]["messages"][0]["content"]
        self.assertNotIn(AI_PROJECT_URL, prompt)
        self.assertNotIn(AI_PROJECT_URL, result.cover_letter)


if __name__ == "__main__":
    unittest.main()
