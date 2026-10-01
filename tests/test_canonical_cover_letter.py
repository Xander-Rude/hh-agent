from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.canonical_cover_letter import (
    cover_letter_guard_issues,
    generate_cover_letter_text,
)


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return SimpleNamespace(
            message=SimpleNamespace(content=self.responses.pop(0))
        )


RESUME = (
    "IT project leader with end-to-end delivery experience from requirements "
    "and planning through development, acceptance, production launch and "
    "further improvement. Managed timelines, risks, changes, budgets, vendors "
    "and cross-functional engineering, analytics, QA, architecture and DevOps "
    "teams. Worked directly with business stakeholders and executives."
)

VALID_BODY = (
    "Hello!\n\n"
    "My experience is centered on end-to-end IT project delivery, from "
    "requirements and planning through coordination of development, acceptance, "
    "production launch and further improvement. I have worked at the boundary "
    "between business and technical teams, managing timelines, risks, changes "
    "and dependencies while keeping decisions visible in project documentation.\n\n"
    "I am especially comfortable in work that requires synchronizing engineering, "
    "analytics, QA, architecture and DevOps while keeping stakeholders aligned "
    "and moving delivery toward a concrete production result."
)


class CanonicalCoverLetterTests(unittest.TestCase):
    def test_guard_rejects_company_and_official_title(self):
        bad = (
            "Hello!\n\n"
            "I am applying for the Project Manager role at Touch Instinct. "
            + VALID_BODY.replace("Hello!\n\n", "")
        )
        issues = cover_letter_guard_issues(
            bad,
            vacancy_title="Project Manager",
            vacancy_company="Touch Instinct",
            resume_text=RESUME,
        )
        self.assertIn("company_name_present", issues)
        self.assertIn("vacancy_title_present", issues)

    def test_guard_rejects_inflected_russian_title_but_not_role_verbs(self):
        title = "\u0420\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c \u043f\u0440\u043e\u0435\u043a\u0442\u043e\u0432"
        company = "Example"
        bad = (
            "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435!\n\n"
            "\u041c\u043e\u0439 \u043e\u043f\u044b\u0442 \u043f\u043e\u0434\u0445\u043e\u0434\u0438\u0442 \u0434\u043b\u044f \u0440\u043e\u043b\u0438 \u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044f \u043f\u0440\u043e\u0435\u043a\u0442\u043e\u0432. "
            + "x" * 400
        )
        self.assertIn(
            "vacancy_title_present",
            cover_letter_guard_issues(
                bad,
                vacancy_title=title,
                vacancy_company=company,
            ),
        )

        good = (
            "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435!\n\n"
            "\u042f \u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u043b IT-\u043f\u0440\u043e\u0435\u043a\u0442\u0430\u043c\u0438 \u043f\u043e\u043b\u043d\u043e\u0433\u043e \u0446\u0438\u043a\u043b\u0430, "
            "\u043a\u043e\u043e\u0440\u0434\u0438\u043d\u0438\u0440\u043e\u0432\u0430\u043b \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0443, QA \u0438 DevOps. "
            + "x" * 400
        )
        self.assertNotIn(
            "vacancy_title_present",
            cover_letter_guard_issues(
                good,
                vacancy_title=title,
                vacancy_company=company,
            ),
        )

    def test_guard_accepts_letter_without_company_or_title(self):
        issues = cover_letter_guard_issues(
            VALID_BODY,
            vacancy_title="Project Manager",
            vacancy_company="Touch Instinct",
            resume_text=RESUME,
        )
        self.assertEqual(issues, [])

    def test_bad_first_draft_is_repaired_once(self):
        bad = (
            "Hello!\n\n"
            "I want the Project Manager role at Touch Instinct. "
            + VALID_BODY.replace("Hello!\n\n", "")
        )
        llm = FakeLLM([bad, VALID_BODY])
        result = generate_cover_letter_text(
            vacancy_title="Project Manager",
            vacancy_company="Touch Instinct",
            vacancy_description=(
                "Lead end-to-end IT projects, align engineering, analytics and "
                "QA, and manage timelines, risks and dependencies."
            ),
            resume_text=RESUME,
            preferences={},
            llm=llm,
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(result.generation_attempts, 2)
        lower = result.final_text.lower()
        self.assertNotIn("touch instinct", lower)
        self.assertNotIn("project manager", lower)
        self.assertTrue(result.final_text.endswith("Aleksandr Rudenko"))

    def test_screening_salary_is_not_exposed_to_cover_writer(self):
        llm = FakeLLM([VALID_BODY])
        generate_cover_letter_text(
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description="Lead IT delivery and stakeholder management.",
            resume_text=RESUME,
            preferences={
                "salary": 300000,
                "currency": "RUB",
                "locations": ["Moscow"],
            },
            llm=llm,
        )
        prompt = llm.calls[0]["messages"][0]["content"]
        self.assertNotIn("300000", prompt)
        self.assertNotIn('"currency": "RUB"', prompt)
        self.assertIn("Moscow", prompt)

    def test_ungrounded_grouped_number_is_repaired_with_exact_diagnostic(self):
        bad = VALID_BODY + "\n\nMy financial expectation is 300 000 RUB."
        llm = FakeLLM([bad, VALID_BODY])
        result = generate_cover_letter_text(
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description=(
                "Lead IT delivery. Please state financial expectations in the cover letter."
            ),
            resume_text=RESUME,
            preferences={"salary": 300000, "currency": "RUB"},
            llm=llm,
        )
        self.assertEqual(result.generation_attempts, 2)
        repair_prompt = llm.calls[1]["messages"][0]["content"]
        self.assertIn("Неподтверждённые числа в черновике: 300000", repair_prompt)
        self.assertNotIn("300000", result.final_text)

    def test_number_from_confirmed_clean_evidence_is_allowed(self):
        body = VALID_BODY + "\n\nI also coordinated a confirmed delivery team of 12 specialists."
        extraction = {
            "requirements": [
                {
                    "source_text": "Coordinate a cross-functional delivery team",
                    "candidate_evidence": "Coordinated a delivery team of 12 specialists",
                    "match_quality": "full",
                    "criticality": "core",
                }
            ]
        }
        issues = cover_letter_guard_issues(
            body,
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            resume_text=RESUME,
            extraction_json=extraction,
        )
        self.assertNotIn("ungrounded_number", issues)

    def test_good_first_draft_does_not_call_repair(self):
        llm = FakeLLM([VALID_BODY])
        result = generate_cover_letter_text(
            vacancy_title="Senior Project Manager",
            vacancy_company="Example Corp",
            vacancy_description=(
                "Lead IT delivery, requirements, risks and a cross-functional "
                "technical team."
            ),
            resume_text=RESUME,
            preferences={},
            llm=llm,
        )
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.generation_attempts, 1)
        self.assertEqual(
            cover_letter_guard_issues(
                result.final_text,
                vacancy_title="Senior Project Manager",
                vacancy_company="Example Corp",
                resume_text=RESUME,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
