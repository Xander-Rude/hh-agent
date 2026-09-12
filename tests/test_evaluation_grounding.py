import unittest

from app.evaluation_grounding import ground_and_decide
from app.models import VacancyEvaluation


def _evaluation(
    *,
    red_flags=None,
    must_have_missing=None,
    role_match=90,
    seniority_match=90,
    domain_match=90,
    responsibility_match=90,
):
    return VacancyEvaluation(
        score=90,
        decision="reject",
        role_match=role_match,
        seniority_match=seniority_match,
        domain_match=domain_match,
        responsibility_match=responsibility_match,
        must_have_missing=must_have_missing or [],
        nice_to_have_missing=[],
        strengths=[],
        gaps=[],
        red_flags=red_flags or [],
        summary="",
        recommendation="",
        cover_letter="",
    )


class EvaluationGroundingPolicyTests(unittest.TestCase):
    def test_office_format_is_not_a_red_flag(self):
        result = _evaluation(
            red_flags=[
                "Вакансия предполагает работу в офисе (Москва), "
                "а кандидат предпочитает удаленный или гибридный формат"
            ]
        )
        vacancy = """Название:
IT Director

Описание:
Работа в офисе в Москве.
"""

        result = ground_and_decide(result, vacancy=vacancy)

        self.assertEqual(result.red_flags, [])
        self.assertEqual(result.gaps, [])
        self.assertEqual(result.decision, "apply")

    def test_unconfirmed_ai_must_have_is_demoted_to_gap(self):
        missing = (
            "Профессиональное владение ИИ и работа с агентами "
            "не подтверждено в резюме"
        )
        result = _evaluation(
            red_flags=[missing],
            must_have_missing=[missing],
        )
        vacancy = """Название:
AI Program Lead

Описание:
Must have: профессиональное владение ИИ и работа с агентами.
"""

        result = ground_and_decide(result, vacancy=vacancy)

        self.assertEqual(result.red_flags, [])
        self.assertIn(missing, result.gaps)
        self.assertEqual(result.decision, "review")

    def test_cto_profile_box_is_not_a_blocking_red_flag(self):
        flag = (
            "Вакансия предполагает роль CTO/CPTO (инженерный лидер, "
            "создающий архитектуру), в то время как профиль кандидата — "
            "Senior Project/Program/Delivery Management"
        )
        result = _evaluation(
            red_flags=[flag],
            role_match=55,
            seniority_match=85,
            domain_match=80,
            responsibility_match=75,
        )
        vacancy = """Название:
CTO/CPTO

Описание:
Инженерный лидер, создающий архитектуру сложных систем.
"""

        result = ground_and_decide(result, vacancy=vacancy)

        self.assertEqual(result.red_flags, [])
        self.assertIn(flag, result.gaps)
        self.assertEqual(result.role_match, 80)
        self.assertIn(result.decision, {"review", "apply"})

    def test_real_language_blocker_remains_a_red_flag(self):
        flag = "Уровень английского B1 ниже обязательного C1"
        result = _evaluation(red_flags=[flag])
        vacancy = """Название:
Program Manager

Описание:
Обязателен английский C1.
"""

        result = ground_and_decide(result, vacancy=vacancy)

        self.assertEqual(result.red_flags, [flag])
        self.assertEqual(result.decision, "reject")

    def test_absolute_eligibility_blocker_is_not_demoted_by_cv_wording(self):
        flag = (
            "Обязательное разрешение на работу не подтверждено в резюме; "
            "позиция предполагает работу в офисе"
        )
        result = _evaluation(red_flags=[flag])
        vacancy = """Название:
IT Director

Описание:
Обязательно разрешение на работу. Работа в офисе.
"""

        result = ground_and_decide(result, vacancy=vacancy)

        self.assertEqual(result.red_flags, [flag])
        self.assertEqual(result.decision, "reject")


if __name__ == "__main__":
    unittest.main()
