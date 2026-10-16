"""Safe, offline regressions for the HH employer-questionnaire copilot."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.hh_questionnaire import (
    QuestionnaireStore,
    card_text,
    extract,
    fill_questions,
    fingerprint,
    salary_answer,
)

Q1 = {
    "id": "402773248",
    "name": "task_402773248_text",
    "kind": "text",
    "question": "От какой минимальной суммы рассматриваете предложения?",
    "options": [],
}
Q2 = {
    "id": "348235958",
    "name": "task_348235958",
    "kind": "choice",
    "question": "Подходит ли вам офис на Калужской?",
    "options": [{"text": "Да", "value": "yes"}, {"text": "Нет", "value": "no"}],
}


class HHQuestionnaireCopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QuestionnaireStore(Path(self.temp.name) / "q.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_salary_is_explicit_net_and_not_inflated(self):
        self.assertEqual(salary_answer(Q1["question"]), "От 350 000 ₽ на руки (net).")
        self.assertIn("комфортный уровень", salary_answer("Ваши зарплатные ожидания от минимума до комфорта?").lower())
        self.assertIsNone(salary_answer("Был ли опыт управления SLA/SLO?"))

    def test_store_requires_approval_and_preserves_edits(self):
        s = self.store
        self.assertTrue(s.save_draft(3493, "clean", [Q1, Q2], [salary_answer(Q1["question"]), ""]))
        self.assertFalse(s.transition(3493, "pending", "approved"))
        self.assertTrue(s.edit(3493, {1: "Да"}))
        self.assertFalse(s.edit(3493, {1: "Не знаю"}))
        self.assertTrue(s.transition(3493, "pending", "approved"))
        self.assertFalse(s.transition(3493, "pending", "approved"))
        self.assertFalse(s.save_draft(3493, "clean", [Q1, Q2], ["", ""]))
        row = s.get(3493)
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["answers"], [salary_answer(Q1["question"]), "Да"])
        self.assertTrue(s.transition(3493, "approved", "sent"))
        self.assertEqual(s.get(3493)["status"], "sent")

    def test_changed_form_requires_new_review_and_keeps_account(self):
        s = self.store
        self.assertTrue(s.save_draft(3493, "clean", [Q1], ["350k"]))
        self.assertTrue(s.transition(3493, "pending", "approved"))
        changed = {**Q1, "question": "Укажите зарплату до вычета НДФЛ"}
        self.assertNotEqual(fingerprint([Q1]), fingerprint([changed]))
        self.assertTrue(s.save_draft(3493, "clean", [changed], [""]))
        self.assertEqual(s.get(3493)["status"], "pending")
        self.assertEqual(s.get(3493)["answers"], [""])
        self.assertTrue(s.save_draft(3493, "old", [changed], [""]))
        self.assertEqual(s.get(3493)["account_key"], "old")

    def test_edit_two_answers_at_once(self):
        s = self.store
        s.save_draft(7, "clean", [Q1, Q2], ["", ""])
        self.assertTrue(s.edit(7, {0: "350 000 ₽ net", 1: "Нет"}))
        self.assertEqual(s.get(7)["answers"], ["350 000 ₽ net", "Нет"])
        self.assertTrue(s.transition(7, "pending", "approved"))

    def test_telegram_card_contains_all_questions(self):
        s = self.store
        s.save_draft(3501, "clean", [Q1, Q2], ["350 000 ₽ net", ""])
        message = card_text(3501, "Руководитель проектов", "Mango", s.get(3501))
        self.assertIn("350 000 ₽ net", message)
        self.assertIn(Q1["question"], message)
        self.assertIn(Q2["question"], message)
        self.assertIn("Не хватает подтверждённых", message)

    def test_structural_extract_is_read_only(self):
        class Page:
            def evaluate(self, script):
                assert 'form[name="vacancy_response"]' in script
                return {"questions": [Q1], "visibility_block": True, "upload_block": False}
        result = extract(Page())
        self.assertTrue(result["visibility_block"])
        self.assertEqual(len(result["questions"]), 1)

    def test_fill_requires_exact_option_and_dom_value(self):
        class Field:
            def __init__(self, value=""):
                self.value, self.checked = value, False

            def is_visible(self):
                return True

            def fill(self, value):
                self.value = value

            def input_value(self):
                return self.value

            def check(self):
                self.checked = True

            def is_checked(self):
                return self.checked

        class Locator:
            def __init__(self, fields):
                self.fields = fields

            def count(self):
                return len(self.fields)

            @property
            def first(self):
                return self.fields[0]

            def nth(self, index):
                return self.fields[index]

        class Page:
            def __init__(self):
                self.inputs = {Q1["name"]: [Field()], Q2["name"]: [Field(), Field()]}

            def locator(self, selector):
                for name, fields in self.inputs.items():
                    if f'[name="{name}"]' in selector:
                        return Locator(fields)
                return Locator([])

        page = Page()
        self.assertTrue(fill_questions(page, [Q1, Q2], ["350 000 ₽ net", "Нет"]))
        self.assertEqual(page.inputs[Q1["name"]][0].value, "350 000 ₽ net")
        self.assertTrue(page.inputs[Q2["name"]][1].checked)
        self.assertFalse(fill_questions(page, [Q1, Q2], ["350 000 ₽ net", "Может быть"]))


if __name__ == "__main__":
    unittest.main()
