import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.evaluator import AI_PROJECT_URL
import telegram_cover_letter_ai_context_patch as ai_patch


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


class TelegramAiCoverLetterContextTests(unittest.TestCase):
    def _cover_module(self, resume_path: Path, cached_letter: str):
        snapshot = SimpleNamespace(
            title="Руководитель отдела внедрения искусственного интеллекта (ИИ)",
            company="Финансовый Дом «Солид»",
            url="https://hh.ru/vacancy/136690075",
            description=(
                "Руководить внедрением искусственного интеллекта, "
                "AI-продуктами и LLM-решениями в бизнес-процессы."
            ),
            cached_cover_letter=cached_letter,
        )

        class CoverLetterResult:
            def __init__(
                self,
                *,
                title,
                company,
                url,
                cover_letter,
                used_cached_evaluation,
            ):
                self.title = title
                self.company = company
                self.url = url
                self.cover_letter = cover_letter
                self.used_cached_evaluation = used_cached_evaluation

        return SimpleNamespace(
            RESUME_PATH=resume_path,
            CoverLetterResult=CoverLetterResult,
            canonicalize_url=lambda value: value,
            _find_cached_snapshot=lambda url: snapshot,
            _fetch_snapshot=lambda url: (_ for _ in ()).throw(
                AssertionError("cached snapshot should be used")
            ),
            _generate_cover_letter=lambda value: (_ for _ in ()).throw(
                AssertionError("cached cover letter should be used")
            ),
            _build_vacancy_text=lambda value: (
                f"Название: {value.title}\n"
                f"Компания: {value.company}\n"
                f"Описание: {value.description}"
            ),
        )

    def test_cached_ai_letter_is_refreshed_with_site_link_and_without_github(self):
        stale_letter = (
            "Здравствуйте!\n\n"
            "Практический опыт с ИИ и LLM дополняю собственным "
            "AI-agent проектом hh-agent на GitHub.\n\n"
            "С уважением,\nАлександр Руденко"
        )
        refreshed_body = (
            "Здравствуйте!\n\n"
            "Практический опыт с ИИ и LLM дополняю собственным AI-agent "
            "проектом, автоматизирующим полный workflow работы с вакансиями: "
            f"{AI_PROJECT_URL}."
        )
        llm = FakeLLM(
            [
                json.dumps({"ai_relevant": True}),
                refreshed_body,
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.txt"
            resume_path.write_text(
                "Program Management, LLM, AI agents.",
                encoding="utf-8",
            )
            cover_module = self._cover_module(resume_path, stale_letter)

            with patch.object(ai_patch, "LLMProvider", return_value=llm):
                ai_patch.install(cover_module)
                result = cover_module.create_cover_letter_for_url(
                    "https://hh.ru/vacancy/136690075"
                )

        self.assertTrue(result.used_cached_evaluation)
        self.assertIn(AI_PROJECT_URL, result.cover_letter)
        self.assertNotIn("github", result.cover_letter.lower())
        self.assertEqual(len(llm.calls), 2)

    def test_verbose_bulleted_ai_letter_is_rewritten_concisely(self):
        stale_letter = (
            "Здравствуйте!\n\n"
            "Управляю крупными IT-проектами и работаю с AI/LLM.\n\n"
            "С уважением,\nАлександр Руденко"
        )
        verbose_body = (
            "Здравствуйте!\n\n"
            "Ключевые факты из моего опыта, релевантные задачам вакансии:\n\n"
            "• Управлял крупными IT-проектами, портфелями и командами.\n"
            "• Работал с LLM и AI-агентами, внедрял технологические изменения.\n"
            "• Развиваю собственный AI-agent проект: "
            f"{AI_PROJECT_URL}.\n\n"
            + ("Дополнительное описание опыта и процессов. " * 35)
        )
        concise_body = (
            "Здравствуйте!\n\n"
            "Мой опыт сочетает управление крупными IT-проектами и практическую "
            "работу с LLM и AI-агентами. Руководил портфелями и командами, "
            "выстраивал взаимодействие бизнеса и IT и доводил изменения до "
            "production.\n\n"
            "Параллельно развиваю собственный AI-agent проект, который "
            "автоматизирует полный workflow работы с вакансиями: "
            f"{AI_PROJECT_URL}. Этот опыт особенно релевантен задачам по "
            "внедрению AI в бизнес-процессы."
        )
        llm = FakeLLM(
            [
                json.dumps({"ai_relevant": True}),
                verbose_body,
                concise_body,
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.txt"
            resume_path.write_text(
                "Program Management, LLM, AI agents.",
                encoding="utf-8",
            )
            cover_module = self._cover_module(resume_path, stale_letter)

            with patch.object(ai_patch, "LLMProvider", return_value=llm):
                ai_patch.install(cover_module)
                result = cover_module.create_cover_letter_for_url(
                    "https://hh.ru/vacancy/136690075"
                )

        body = ai_patch._strip_existing_signature(result.cover_letter)
        self.assertEqual(len(llm.calls), 3)
        self.assertIn(AI_PROJECT_URL, body)
        self.assertNotIn("github", body.lower())
        self.assertNotIn("•", body)
        self.assertNotIn("ключевые факты", body.lower())
        self.assertLessEqual(len(body), ai_patch.AI_COVER_HARD_MAX_CHARS)

    def test_non_ai_cached_letter_is_left_unchanged(self):
        cached_letter = (
            "Здравствуйте!\n\n"
            "Управляю IT-проектами, бюджетами, рисками и командами.\n\n"
            "С уважением,\nАлександр Руденко"
        )
        llm = FakeLLM(
            [json.dumps({"ai_relevant": False})]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.txt"
            resume_path.write_text(
                "Program Management, PMO.",
                encoding="utf-8",
            )
            cover_module = self._cover_module(resume_path, cached_letter)
            cover_module._find_cached_snapshot = lambda url: SimpleNamespace(
                title="Senior Project Manager",
                company="Компания",
                url=url,
                description="Управление IT-проектами, сроками и бюджетами.",
                cached_cover_letter=cached_letter,
            )

            with patch.object(ai_patch, "LLMProvider", return_value=llm):
                ai_patch.install(cover_module)
                result = cover_module.create_cover_letter_for_url(
                    "https://hh.ru/vacancy/1"
                )

        self.assertEqual(result.cover_letter, cached_letter)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
