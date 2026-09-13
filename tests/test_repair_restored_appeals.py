import json
import unittest
from types import SimpleNamespace

from repair_restored_appeals import repair_result


RESUME = "Program Management PMO управление портфелем 30+ проектов управление IT-проектами C-level"


def make_evaluation(red_flags, gaps=None, must_have=None):
    return SimpleNamespace(
        score=84,
        decision="reject",
        role_match=82,
        seniority_match=86,
        domain_match=80,
        responsibility_match=90,
        must_have_missing=json.dumps(must_have or [], ensure_ascii=False),
        nice_to_have_missing="[]",
        strengths=json.dumps(["Управление портфелем IT-проектов"], ensure_ascii=False),
        gaps=json.dumps(gaps or [], ensure_ascii=False),
        red_flags=json.dumps(red_flags or [], ensure_ascii=False),
        summary="Кандидат не подходит для данной вакансии.",
        recommendation="Не стоит откликаться.",
        cover_letter="",
    )


class RepairRestoredAppealsTests(unittest.TestCase):
    def test_management_ai_ml_false_stack_reject_is_repaired(self):
        evaluation = make_evaluation(
            ["Критический mismatch по техническому стеку: Python, Git, Data Science; профиль — чистый менеджмент."],
            ["Нет подтвержденных навыков Python и Git."],
            ["Python, Git, основы обучения моделей"],
        )
        vacancy = (
            "Название: Руководитель направления развития AI/ML\n"
            "Описание: управление портфелем ИИ-проектов, формирование требований, координация команд. "
            "Требуется знание Python, Git и основ обучения моделей."
        )
        result, before, after = repair_result(evaluation, resume=RESUME, vacancy_text=vacancy)
        self.assertEqual(before["decision"], "reject")
        self.assertEqual(after["red_flags"], [])
        self.assertEqual(after["must_have_missing"], [])
        self.assertEqual(after["gaps"], [])
        self.assertEqual(result.decision, "apply")

    def test_real_hands_on_requirement_is_kept(self):
        evaluation = make_evaluation(
            ["Требуется лично писать код на Python и обучать ML-модели."],
            must_have=["Python и hands-on ML-разработка"],
        )
        vacancy = (
            "Название: Руководитель направления развития AI/ML\n"
            "Описание: управление командой; необходимо лично писать код на Python и обучать модели машинного обучения."
        )
        result, _, after = repair_result(evaluation, resume=RESUME, vacancy_text=vacancy)
        self.assertTrue(after["red_flags"])
        self.assertTrue(after["must_have_missing"])
        self.assertEqual(result.decision, "reject")


if __name__ == "__main__":
    unittest.main()
