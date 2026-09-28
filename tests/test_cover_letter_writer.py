import json
import unittest
from types import SimpleNamespace

from app.cover_letter_runtime import clean_cover_fact_bank, legacy_cover_fact_bank
from app.cover_letter_writer import (
    validate_human_cover_letter,
    write_human_cover_letter,
)


class FakeLLM:
    def __init__(self, payload):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls = 0

    def chat(self, messages, format_schema=None):
        index = min(self.calls, len(self.payloads) - 1)
        payload = self.payloads[index]
        self.calls += 1
        return SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps(payload, ensure_ascii=False)
            )
        )


class HumanCoverWriterTests(unittest.TestCase):
    def setUp(self):
        self.fallback = (
            "Здравствуйте!\n\n"
            "Вёл IT-проекты полного цикла - от требований и планирования "
            "до релиза, production и дальнейшего развития.\n\n"
            "Александр Руденко"
        )
        self.facts = [
            "Отвечал за сроки, риски, зависимости, ресурсы и бюджет проекта.",
            "В МТС сократил Time-to-Market примерно со 100 до 24 дней.",
        ]

    def test_accepts_warm_grounded_letter(self):
        llm = FakeLLM(
            {
                "letter_body": (
                    "Здравствуйте!\n\n"
                    "Мне близок формат, где нужно не просто вести статусы, а держать "
                    "сроки и зависимости между несколькими участниками. "
                    "В своих проектах я отвечал за сроки, риски, зависимости, ресурсы "
                    "и бюджет. В МТС сократил Time-to-Market примерно со 100 до 24 дней. "
                    "Мне нравится такой формат работы: здесь есть о чём предметно "
                    "поговорить про зависимости и ритм delivery."
                ),
                "used_fact_ids": ["F1", "F2"],
            }
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description=(
                "Нужно координировать несколько потоков, управлять сроками, "
                "рисками и межкомандными зависимостями."
            ),
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertNotEqual(result, self.fallback)
        self.assertIn("Мне близок формат", result)
        self.assertIn("100 до 24", result)
        self.assertTrue(result.endswith("Александр Руденко"))

    def test_validates_persisted_human_letter_for_exact_preview_reuse(self):
        current = (
            "Здравствуйте!\n\n"
            "Мне близка работа, где нужно держать сроки и зависимости между "
            "несколькими участниками. В своих проектах я отвечал за сроки, "
            "риски, зависимости, ресурсы и бюджет. Здесь особенно интересно "
            "предметно поговорить о том, как устроен ритм delivery.\n\n"
            "Александр Руденко"
        )

        result = validate_human_cover_letter(
            current,
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            allowed_facts=self.facts,
        )

        self.assertEqual(result, current)

    def test_persisted_cold_template_is_not_accepted_as_reviewed_human_letter(self):
        cold = (
            "Здравствуйте!\n\n"
            "У меня есть релевантный опыт для такого типа IT-проектов. "
            "Отвечал за сроки, риски и ресурсы проекта.\n\n"
            "Александр Руденко"
        )

        result = validate_human_cover_letter(
            cold,
            vacancy_title="Project Manager",
            vacancy_company="Example Corp",
            allowed_facts=self.facts,
        )

        self.assertIsNone(result)

    def test_rejects_old_canned_language(self):
        llm = FakeLLM(
            {
                "letter_body": (
                    "Здравствуйте! У меня есть релевантный опыт для такого типа "
                    "IT-проектов. Отвечал за сроки, риски и ресурсы проекта. "
                    "Буду рад рассказать подробнее о похожих проектах."
                ),
                "used_fact_ids": ["F1"],
            }
        )

        result = write_human_cover_letter(
            account_key="old",
            vacancy_title="Менеджер проектов",
            vacancy_company="Example Corp",
            vacancy_description="Управление сроками и рисками.",
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertEqual(result, self.fallback)

    def test_rejects_unsupported_numbers(self):
        llm = FakeLLM(
            {
                "letter_body": (
                    "Здравствуйте! Мне близка задача выстроить предсказуемый delivery. "
                    "Я управлял командой из 70 человек и отвечал за сроки, риски и "
                    "ресурсы. Такой формат работы мне хорошо знаком и интересен."
                ),
                "used_fact_ids": ["F1"],
            }
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Delivery, сроки и риски.",
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertEqual(result, self.fallback)

    def test_repairs_unsupported_causal_link(self):
        llm = FakeLLM(
            [
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка задача держать delivery предсказуемым. "
                        "В МТС сократил Time-to-Market со 100 до 24 дней благодаря "
                        "жёсткому контролю рисков. Такой формат мне хорошо знаком."
                    ),
                    "used_fact_ids": ["F1", "F2"],
                },
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка задача держать delivery предсказуемым "
                        "и не терять зависимости между командами. В своих проектах я "
                        "отвечал за сроки, риски, зависимости, ресурсы и бюджет. В МТС "
                        "сократил Time-to-Market со 100 до 24 дней. Мне нравится такой "
                        "формат: здесь есть о чём предметно поговорить про зависимости "
                        "и ритм delivery."
                    ),
                    "used_fact_ids": ["F1", "F2"],
                },
            ]
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Сроки, риски и зависимости нескольких команд.",
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertEqual(llm.calls, 2)
        self.assertNotIn("благодаря", result.lower())
        self.assertIn("100 до 24", result)


    def test_repairs_subtle_unsupported_causal_link(self):
        llm = FakeLLM(
            [
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка работа со сложным техническим контекстом. "
                        "Я работал с highload-системами, API и архитектурой, что помогает "
                        "мне эффективно планировать работу технической команды. "
                        "Будет интересно предметно поговорить о задачах."
                    ),
                    "used_fact_ids": ["F1"],
                },
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка работа, где проектный контур тесно связан "
                        "с техническими командами. Я отвечал за сроки, риски, зависимости, "
                        "ресурсы и бюджет. Будет интересно предметно поговорить о том, "
                        "как у вас устроены зависимости между командами."
                    ),
                    "used_fact_ids": ["F1"],
                },
            ]
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Технический проект, риски и межкомандные зависимости.",
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertEqual(llm.calls, 2)
        self.assertNotIn("что помогает", result.lower())

    def test_clean_fact_bank_prefers_vendor_and_budget_evidence(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Управление внутренними командами и подрядчиками; закупки, заключение договоров",
                    "candidate_evidence": "Управление подрядчиками, закупки RFP/RFQ и договоры.",
                },
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Составление roadmap, ведение бюджета и планирование ресурсов",
                    "candidate_evidence": "Ведение бюджетов, ресурсов и roadmap.",
                },
            ]
        }

        facts = clean_cover_fact_bank(
            vacancy_title="Старший менеджер проектов",
            vacancy_description="Комплексный hardware/software проект.",
            extraction_json=extraction,
        )

        self.assertTrue(any("подрядчиками и закупками" in fact for fact in facts))
        self.assertTrue(any("roadmap, бюджеты и ресурсное планирование" in fact for fact in facts))
        self.assertFalse(any("100 до 24" in fact for fact in facts))

    def test_generic_project_does_not_force_ttm_into_fact_banks(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Опыт ведения комплексных IT-проектов",
                    "candidate_evidence": "Полный цикл IT-проектов от требований до production.",
                }
            ]
        }
        clean_facts = clean_cover_fact_bank(
            vacancy_title="Project Manager",
            vacancy_description="Ведение IT-проектов полного цикла.",
            extraction_json=extraction,
        )
        old_facts = legacy_cover_fact_bank(
            vacancy_title="Project Manager",
            vacancy_description="Ведение IT-проектов полного цикла.",
            stored_text="",
            strengths=["Опыт управления IT-проектами полного цикла."],
        )

        self.assertFalse(any("100 до 24" in fact for fact in clean_facts))
        self.assertFalse(any("100 до 24" in fact for fact in old_facts))

    def test_ai_fact_requires_project_url(self):
        ai_fact = (
            "Развиваю собственный AI-agent для автоматизации работы с вакансиями: "
            "https://rudenko.one/hh-agent.html"
        )
        llm = FakeLLM(
            [
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка задача сделать AI-delivery более "
                        "предсказуемым. Развиваю собственный AI-agent для автоматизации "
                        "работы с вакансиями. Такой практический контекст помогает мне "
                        "понимать ограничения AI-проектов."
                    ),
                    "used_fact_ids": ["F1"],
                },
                {
                    "letter_body": (
                        "Здравствуйте! Мне близка задача сделать AI-delivery более "
                        "предсказуемым. Развиваю собственный AI-agent для автоматизации "
                        "работы с вакансиями: https://rudenko.one/hh-agent.html. "
                        "Мне нравится работать там, где новый AI-контур нужно довести "
                        "до устойчивого процесса."
                    ),
                    "used_fact_ids": ["F1"],
                },
            ]
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="AI Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Управление delivery AI-проектов.",
            safe_draft=self.fallback,
            allowed_facts=[ai_fact],
            llm=llm,
        )

        self.assertEqual(llm.calls, 2)
        self.assertIn("https://rudenko.one/hh-agent.html", result)

    def test_rejects_company_or_title_echo(self):
        llm = FakeLLM(
            {
                "letter_body": (
                    "Здравствуйте! В Example Corp мне интересна именно роль Senior "
                    "Project Manager. Я отвечал за сроки, риски, зависимости, ресурсы "
                    "и бюджет проекта. Мне близок такой формат delivery."
                ),
                "used_fact_ids": ["F1"],
            }
        )

        result = write_human_cover_letter(
            account_key="clean",
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Delivery, сроки и риски.",
            safe_draft=self.fallback,
            allowed_facts=self.facts,
            llm=llm,
        )

        self.assertEqual(result, self.fallback)

    def test_clean_fact_bank_surfaces_portfolio_only_when_grounded(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "experience",
                    "match_quality": "full",
                    "source_text": (
                        "Опыт управления портфелем нескольких параллельных проектов"
                    ),
                    "candidate_evidence": (
                        "Подтверждено управление портфелем 30+ IT-проектов."
                    ),
                }
            ]
        }

        facts = clean_cover_fact_bank(
            vacancy_title="AI Project Manager",
            vacancy_description="Управление портфелем AI-проектов.",
            extraction_json=extraction,
        )

        self.assertTrue(any("30+ IT-проектов" in fact for fact in facts))


if __name__ == "__main__":
    unittest.main()
