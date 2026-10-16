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
    ungrounded_numeric_claims,
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

    def test_numeric_grounding_catches_cross_project_number_reuse(self):
        evidence = (
            "Сократил Time-to-Market: ~100 → ~24 дня (-76%). "
            "Перевёл платформу от монолитной архитектуры к Cloud Native "
            "с выделением общих переиспользуемых сервисов. "
            "Управлял 6 продуктовыми end-to-end командами. "
            "Обеспечил SLA 99,99%."
        )
        self.assertEqual(
            ungrounded_numeric_claims("Перевёл платформу на более 100 микросервисов.", evidence),
            ["quantity:100 микросервисов"],
        )
        self.assertEqual(
            ungrounded_numeric_claims("В компании построил 135 микросервисов.", evidence),
            ["number:135", "quantity:135 микросервисов"],
        )
        self.assertEqual(
            ungrounded_numeric_claims("Управлял 6 командами, SLA 99.99%.", evidence),
            [],
        )
        self.assertEqual(
            ungrounded_numeric_claims("Сократил Time-to-Market со 100 до 24 дней.", evidence),
            [],
        )

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
        self.assertIn("Нужно твоё подтверждение", message)
        self.assertNotIn(" / ", message)
        self.assertIn("▫️ 1. Да", message)
        self.assertIn("▫️ 2. Нет", message)

    def test_structural_extract_is_read_only(self):
        class Page:
            def evaluate(self, script):
                assert 'form[name="vacancy_response"]' in script
                return {"questions": [Q1], "visibility_block": True, "upload_block": False}
        result = extract(Page())
        self.assertTrue(result["visibility_block"])
        self.assertEqual(len(result["questions"]), 1)

    @unittest.skipUnless(
        __import__("os").environ.get("HH_APPLY_DOM_TESTS") == "1",
        "Browser DOM regression requires HH_APPLY_DOM_TESTS=1",
    )
    def test_collapsed_visibility_warning_is_not_a_real_hh_blocker(self):
        """Reproduces MANGO #3493: warning exists in DOM but max-height:0."""
        from playwright.sync_api import sync_playwright

        form = """<form name="vacancy_response">
          <div>
            <p>От какой минимальной суммы рассматриваете предложения?</p>
            <textarea name="task_9001"></textarea>
          </div>
          <div data-qa="hidden-resume-warning" style="max-height: 0px; overflow: hidden">
            <div>Чтобы откликнуться на эту вакансию, поменяйте видимость резюме
            на «Видно всем работодателям, зарегистрированным на hh.ru»</div>
          </div>
          <button type="submit">Откликнуться</button>
        </form>"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(form)
                collapsed = extract(page)
                self.assertIsNotNone(collapsed)
                self.assertEqual(len(collapsed["questions"]), 1)
                self.assertFalse(collapsed["visibility_block"])
                self.assertFalse(collapsed["upload_block"])

                page.locator('[data-qa="hidden-resume-warning"]').evaluate(
                    "(el) => el.style.maxHeight = '150px'"
                )
                expanded = extract(page)
                self.assertTrue(expanded["visibility_block"])

                page.locator('[data-qa="hidden-resume-warning"]').evaluate(
                    "(el) => {el.style.maxHeight = '150px'; el.style.display = 'none'}"
                )
                hidden = extract(page)
                self.assertFalse(hidden["visibility_block"])

                page.locator('[data-qa="hidden-resume-warning"]').evaluate(
                    "(el) => el.removeAttribute('data-qa')"
                )
                # Unknown HH markup remains conservative, not silently bypassed.
                page.locator('[style*="max-height"]').evaluate(
                    "(el) => {el.style.display = 'block'; el.style.maxHeight = '150px'}"
                )
                unrecognized = extract(page)
                self.assertTrue(unrecognized["visibility_block"])
            finally:
                browser.close()

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


    def test_old_evaluation_strengths_do_not_enter_hh_answer_prompt(self):
        from unittest.mock import patch
        from app.sber_screening import ScreeningContext
        from app.hh_questionnaire import generate_answers

        stale = "Опыт трансформации монолита (100+ микросервисов)"
        context = ScreeningContext(
            application_id=3493,
            vacancy_title="Project Manager",
            company="MANGO OFFICE",
            vacancy_description="Развитие высоконагруженной платформы.",
            selected_resume_title="Руководитель проектов",
            evaluation_strengths=[stale],
            verified_facts=["13+ лет в IT."],
            resume_text=(
                "За счёт организации продуктовых команд "
                "сократил Time-to-Market изменений с ~100 до ~24 дней. "
                "Участвовал в архитектурных обсуждениях на уровне управления проектом."
            ),
        )

        class LLM:
            prompts = []

            def chat(self, *, messages, format_schema):
                self.prompts.append(messages[0]["content"])
                return {"message": {"content": (
                    '{"mode":"answer","answer":"Управлял переходом на 100 микросервисов."}'
                )}}

        fake = LLM()
        with (
            patch("app.sber_screening.load_context", return_value=context),
            patch("app.llm.LLMProvider", return_value=fake),
        ):
            answers = generate_answers(3493, [
                {"id": "25", "kind": "text", "question": "Как участвовали в архитектуре?"}
            ])
        self.assertEqual(answers, [""])
        self.assertNotIn(stale, fake.prompts[0])
        self.assertIn("~100 до ~24 дней", fake.prompts[0])

    def test_grounded_metrics_survive_without_model_evaluation(self):
        from app.hh_questionnaire import ungrounded_numeric_claims

        pdf_text = (
            "В Ростелекоме управлял развитием платформы видеонаблюдения. "
            "Сотни тысяч камер, SLA 99,99%. "
            "Сократил Time-to-Market с ~100 до ~24 дней."
        )
        self.assertEqual(
            ungrounded_numeric_claims("Работал с платформой видеонаблюдения с SLA 99,99%.", pdf_text), [],
        )
        self.assertIn(
            "quantity:100 микросервисов",
            ungrounded_numeric_claims("Перевёл платформу на 100 микросервисов.", pdf_text),
        )


    def test_senior_pm_situational_choices_are_proposals_and_other_llm_paths_untouched(self):
        from unittest.mock import patch
        from app.hh_questionnaire import generate_answers
        from app.hh_questionnaire_decisions import is_situational

        questions = [
            {
                "id": "101", "kind": "choice", "question":
                "Руководитель клиентского сервиса просит новую кнопку в CRM. Что вы сделаете первым?",
                "options": [
                    {"text": "Сразу напишу требования разработке."},
                    {"text": "Разберу реальные случаи потери времени менеджеров."},
                    {"text": "Оценю сложность настройки CRM."},
                ],
            },
            {
                "id": "102", "kind": "choice", "question":
                "Менеджеры вручную переносят данные между двумя системами. Что предложите?",
                "options": [
                    {"text": "Немедленно поставлю интеграцию в план."},
                    {"text": "Проверю необходимость переноса и альтернативы."},
                    {"text": "Оставлю процесс ручным навсегда."},
                ],
            },
            {
                "id": "103", "kind": "choice", "question":
                "После запуска новой CRM менеджеры жалуются на неудобства. Как проверите, что менять?",
                "options": [
                    {"text": "Сразу соберу пожелания."},
                    {"text": "Попрошу общий список от начальника."},
                    {"text": "Посмотрю выполнение типовых задач и причины затруднений."},
                ],
            },
        ]
        self.assertTrue(all(is_situational(q["question"]) for q in questions))

        class FakeLLM:
            def __init__(self):
                self.indices = iter((2, 2, 3))
                self.prompts = []

            def chat(self, *, messages, format_schema):
                self.prompts.append(messages[0]["content"])
                return {"message": {"content": __import__("json").dumps({
                    "mode": "select", "option_index": next(self.indices),
                    "reason": "Сначала измерю причину и экономику, затем выберу реализацию.",
                }, ensure_ascii=False)}}

        fake = FakeLLM()
        with (
            patch("app.llm.LLMProvider", return_value=fake),
            patch("app.sber_screening.load_context", side_effect=AssertionError(
                "Hypothetical questions must not require resume evidence"
            )),
        ):
            answers = generate_answers(42, questions)
        self.assertEqual(answers, [
            questions[0]["options"][1]["text"],
            questions[1]["options"][1]["text"],
            questions[2]["options"][2]["text"],
        ])
        self.assertTrue(all("draft_reason" in q for q in questions))
        self.assertIn("ИИ-агенты", fake.prompts[1])
        self.assertIn("ROI/TCO", fake.prompts[0])
        self.assertTrue(self.store.valid_answers(questions, answers))
        for q in questions:
            q.setdefault("name", "task_" + q["id"])
        self.store.save_draft(42, "old", questions, answers)
        card = card_text(42, "Руководитель проектов", "Example", self.store.get(42))
        self.assertIn("✅ <b>2. Разберу реальные", card)
        self.assertIn("✅ <b>3. Посмотрю", card)
        self.assertNotIn(" / ", card)
        self.assertNotIn("Не хватает подтверждённых", card)

    def test_contact_salary_and_personal_consent_are_separate_from_llm(self):
        import os
        from unittest.mock import patch
        from app.hh_questionnaire import generate_answers
        from app.hh_questionnaire_decisions import telegram_contact_answer

        questions = [
            {"id": "1", "kind": "text", "question": "Уточните зарплатные ожидания."},
            {"id": "2", "kind": "text", "question": "Готов(а) ли ты работать в офисе 4/1?"},
            {"id": "3", "kind": "text", "question": "Укажи Telegram для обратной связи."},
            {"id": "4", "kind": "choice", "question": "Подходит ли вам офис на Курской?",
             "options": [{"text": "Да"}, {"text": "Нет"}]},
        ]
        with (
            patch.dict(os.environ, {"HH_APPLICANT_TELEGRAM": "https://t.me/xander_rude"}),
            patch("app.llm.LLMProvider", side_effect=AssertionError("No LLM needed")),
        ):
            self.assertEqual(telegram_contact_answer(questions[2]["question"]), "@xander_rude")
            answers = generate_answers(42, questions)
        self.assertEqual(answers, ["От 350 000 ₽ на руки (net).", "", "@xander_rude", ""])
        self.assertFalse(self.store.valid_answers(questions, answers))
        for q in questions:
            q.setdefault("name", "task_" + q["id"])
        self.store.save_draft(42, "clean", questions, answers)
        card = card_text(42, "PM", "Example", self.store.get(42))
        self.assertIn("@xander_rude", card)
        self.assertIn("Нужно твоё подтверждение", card)

    def test_invalid_choice_index_never_invents_a_hh_option(self):
        from app.hh_questionnaire_decisions import choose_situational_option

        question = {
            "kind": "choice", "question":
            "Клиент просит срочно внести изменение. Что вы сделаете первым?",
            "options": [{"text": "Сразу реализую"}, {"text": "Уточню бизнес-эффект"}],
        }

        class InvalidLLM:
            def chat(self, *, messages, format_schema):
                return {"message": {"content": '{"mode":"select","option_index":9,"reason":"ошибка"}'}}

        self.assertEqual(choose_situational_option(question, InvalidLLM()), ("", ""))

    def test_biography_still_requires_resume_and_numeric_grounding(self):
        from unittest.mock import patch
        from app.sber_screening import ScreeningContext
        from app.hh_questionnaire import generate_answers

        context = ScreeningContext(
            application_id=42, vacancy_title="PM", company="Example",
            vacancy_description="platform", selected_resume_title="РП",
            evaluation_strengths=["100 микросервисов"],
            verified_facts=["13+ лет IT"],
            resume_text="Сократил Time-to-Market со 100 до 24 дней.",
        )

        class FakeLLM:
            def chat(self, *, messages, format_schema):
                self.prompt = messages[0]["content"]
                return {"message": {"content":
                    '{"mode":"answer","answer":"Построил 100 микросервисов."}'}}

        fake = FakeLLM()
        with (
            patch("app.sber_screening.load_context", return_value=context),
            patch("app.llm.LLMProvider", return_value=fake),
        ):
            result = generate_answers(42, [
                {"id": "5", "kind": "text",
                 "question": "Какой у вас опыт с микросервисной архитектурой?"}
            ])
        self.assertEqual(result, [""])
        self.assertNotIn("evaluation_strengths", fake.prompt)


if __name__ == "__main__":
    unittest.main()
