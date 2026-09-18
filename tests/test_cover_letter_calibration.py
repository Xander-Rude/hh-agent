import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.evaluator import VacancyEvaluator


RESUME = (
    "Управление IT-проектами полного цикла, требования, roadmap, риски, "
    "интеграции, работа с бизнес-заказчиками. PMO, портфель 30+ IT-проектов, "
    "команды до 70 человек, найм 40+ специалистов, C-level stakeholders."
)

VACANCY = (
    "Название: Product manager / Project manager (IT)\n"
    "Описание: управление требованиями, roadmap, интеграциями, delivery, "
    "взаимодействие с бизнес-заказчиками. Есть продуктовый фокус."
)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps(
                    self.payload,
                    ensure_ascii=False,
                )
            )
        )


def evaluation_payload(cover_letter: str) -> dict:
    return {
        "score": 96,
        "decision": "apply",
        "role_match": 95,
        "seniority_match": 95,
        "domain_match": 90,
        "responsibility_match": 95,
        "must_have_missing": [],
        "nice_to_have_missing": [],
        "strengths": [
            "Опыт работы с требованиями, roadmap и delivery.",
            "Опыт сложных интеграций и взаимодействия с бизнес-заказчиками.",
            "Подтверждён people management: команды до 70 человек, найм 40+ специалистов.",
        ],
        "gaps": [
            "Основной профиль кандидата сфокусирован на Project/Delivery Management, "
            "а не на чистом Product Management."
        ],
        "red_flags": [],
        "summary": "Хорошее пересечение по проектной части, продуктовый фокус смежный.",
        "recommendation": "Разумно рассмотреть отклик.",
        "ai_relevant": False,
        "cover_letter": cover_letter,
    }


class CoverLetterCalibrationTests(unittest.TestCase):
    def evaluate(self, cover_letter: str):
        llm = FakeLLM(
            evaluation_payload(
                cover_letter
            )
        )
        evaluator = VacancyEvaluator(
            llm=llm
        )

        with patch.dict(
            os.environ,
            {"HH_ENABLE_AI_PROJECT_COVER_LETTER": "false"},
        ):
            result = evaluator.evaluate(
                resume=RESUME,
                vacancy=VACANCY,
                preferences={},
            )

        return result, llm

    def test_stacked_scale_facts_fall_back_to_conservative_letter(self):
        result, _ = self.evaluate(
            "Здравствуйте!\n\n"
            "Управлял портфелем 30+ IT-проектов, командами до 70 человек "
            "и работал с C-level. При этом отвечал за требования, roadmap "
            "и delivery до production."
        )

        self.assertIn(
            "Мой основной профиль - управление IT-проектами",
            result.cover_letter,
        )
        self.assertNotIn(
            "30+",
            result.cover_letter,
        )
        self.assertNotIn(
            "70 человек",
            result.cover_letter,
        )
        self.assertNotIn(
            "C-level",
            result.cover_letter,
        )
        self.assertIn(
            "требованиями, roadmap и delivery",
            result.cover_letter,
        )

    def test_promotional_phrasing_falls_back(self):
        result, _ = self.evaluate(
            "Здравствуйте!\n\n"
            "У меня многолетний опыт управления IT-проектами на уровне senior/lead. "
            "В этой роли особенно близки требования, roadmap, интеграции и delivery."
        )

        self.assertNotIn(
            "многолетний опыт",
            result.cover_letter.lower(),
        )
        self.assertNotIn(
            "senior/lead",
            result.cover_letter.lower(),
        )
        self.assertIn(
            "Мой основной профиль",
            result.cover_letter,
        )

    def test_single_scale_fact_is_not_rejected_by_guard(self):
        original = (
            "Здравствуйте!\n\n"
            "Вёл портфель 30+ IT-проектов, а для этой позиции мне ближе всего "
            "работа с требованиями, roadmap и интеграциями."
        )

        result, _ = self.evaluate(
            original
        )

        self.assertIn(
            "Вёл портфель 30+ IT-проектов",
            result.cover_letter,
        )
        self.assertNotIn(
            "Мой основной профиль - управление IT-проектами",
            result.cover_letter,
        )

    def test_prompt_requires_calibrated_non_salesy_letter(self):
        result, llm = self.evaluate(
            "Здравствуйте!\n\n"
            "Мой основной профиль - управление IT-проектами. "
            "В этой позиции мне близки требования, roadmap и интеграции."
        )

        self.assertTrue(
            result.cover_letter
        )
        prompt = llm.calls[0]["messages"][0]["content"]

        self.assertIn(
            "а не максимизировать впечатление о кандидате",
            prompt,
        )
        self.assertIn(
            "Выбери только 1-2 наиболее релевантных факта",
            prompt,
        )
        self.assertIn(
            "Не компенсируй gap масштабом прошлых ролей",
            prompt,
        )
        self.assertIn(
            "допустим максимум один такой scale-факт",
            prompt,
        )
        self.assertIn(
            "не маскируй это",
            prompt,
        )
        self.assertIn(
            "не называй кандидата Product Manager",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
