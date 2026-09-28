import json
import unittest
from types import SimpleNamespace

from app.cover_letter_runtime import clean_cover_fact_bank
from app.cover_letter_writer import write_human_cover_letter


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
                    "и бюджет. В МТС такой подход помог сократить Time-to-Market "
                    "примерно со 100 до 24 дней. Мне нравится работа, где порядок "
                    "в delivery напрямую помогает команде быстрее доводить изменения "
                    "до production."
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
                        "сократил Time-to-Market со 100 до 24 дней. Люблю формат, где "
                        "управление проектом помогает команде не вязнуть в ручном контроле."
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
