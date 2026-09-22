from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.sber_screening import ScreeningContext, build_prompt, mask_secret
from app.sber_screening_store import SberScreeningStore


class SberScreeningStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SberScreeningStore(
            Path(self.temp.name) / "screening.sqlite3"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_arm_supersedes_previous_session(self) -> None:
        first = self.store.arm(100)
        second = self.store.arm(200)

        self.assertEqual(
            self.store.get_session(int(first["id"]))["status"],
            "superseded",
        )
        self.assertEqual(
            self.store.get_active_session()["application_id"],
            200,
        )
        self.assertEqual(second["status"], "armed")

    def test_turn_requires_explicit_approval(self) -> None:
        session = self.store.arm(123)
        turn = self.store.create_turn(
            session_id=int(session["id"]),
            external_message_id=77,
            question="Расскажите об опыте.",
            suggested_answer="Есть подтвержденный опыт.",
            confidence="high",
            reason="resume facts",
        )

        self.assertEqual(turn["status"], "pending")
        self.assertEqual(self.store.approved_turns(), [])

        self.assertTrue(self.store.approve_suggestion(int(turn["id"])))
        approved = self.store.approved_turns()
        self.assertEqual(len(approved), 1)
        self.assertTrue(
            str(approved[0]["approved_payload"]).startswith("text:")
        )

    def test_button_choice_is_bounded(self) -> None:
        session = self.store.arm(123)
        turn = self.store.create_turn(
            session_id=int(session["id"]),
            external_message_id=88,
            question="Выберите вариант",
            options=["Да", "Нет"],
        )
        turn_id = int(turn["id"])

        self.assertFalse(self.store.approve_button(turn_id, 5))
        self.assertTrue(self.store.approve_button(turn_id, 1))
        self.assertEqual(
            self.store.get_turn(turn_id)["approved_payload"],
            "button:1",
        )


class SberScreeningPromptTests(unittest.TestCase):
    def test_mask_secret_does_not_expose_middle(self) -> None:
        value = "aT6Ym3zWpSLJ22yQc9isCjfc"
        masked = mask_secret(value)
        self.assertTrue(masked.startswith("aT6Y"))
        self.assertTrue(masked.endswith("Cjfc"))
        self.assertNotIn("m3zWpSLJ22yQc9is", masked)

    def test_prompt_is_fail_closed(self) -> None:
        context = ScreeningContext(
            application_id=1752,
            vacancy_title="Руководитель проектов (программа AI PDLC)",
            company="Сбер",
            vacancy_description="Нужен опыт управления AI-проектами.",
            selected_resume_title="Senior Project Manager",
            evaluation_strengths=["Опыт управления IT-проектами полного цикла."],
            verified_facts=["13+ лет опыта в IT."],
        )
        prompt = build_prompt(
            context,
            "Какой у вас опыт с AI PDLC?",
            history=[],
        )

        self.assertIn("ТОЛЬКО факты", prompt)
        self.assertIn("needs_user", prompt)
        self.assertIn("не придумывай", prompt.lower())
        self.assertIn("Руководитель проектов (программа AI PDLC)", prompt)


if __name__ == "__main__":
    unittest.main()
