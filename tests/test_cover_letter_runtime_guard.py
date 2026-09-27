import unittest

from app.cover_letter_runtime import (
    AI_PROJECT_URL,
    build_clean_cover_letter,
    build_legacy_vacancy_cover_letter,
    calibrate_stored_cover_letter,
    is_oversold_cover_letter,
    is_vacancy_bound_cover_letter,
)


OLD_OVERSOLD = (
    "Здравствуйте! У меня многолетний опыт управления IT-проектами, программами "
    "и delivery на уровне senior/lead. Из наиболее релевантного для этой позиции: "
    "управлял портфелем 30+ проектов; управлял крупными IT-командами до 70 человек "
    "и наймом 40+ специалистов; работал с C-level, CEO-1 и бизнес-заказчиками. "
    "В работе веду полный управленческий цикл: цели и roadmap, требования, сроки, "
    "риски, изменения, ресурсы, бюджет, взаимодействие со стейкхолдерами."
)

STRENGTHS = [
    "Опыт управления IT-проектами полного цикла (от требований до production)",
    "Опыт работы в финтех-секторе и с высоконагруженными системами",
    "Опыт работы с C-level, CEO-1 и бизнес-заказчиками",
]


class RuntimeCoverLetterCalibrationTests(unittest.TestCase):
    def test_detects_old_oversold_letter(self):
        self.assertTrue(is_oversold_cover_letter(OLD_OVERSOLD))

    def test_replaces_old_oversold_letter_with_direct_evidence(self):
        result = calibrate_stored_cover_letter(
            OLD_OVERSOLD,
            STRENGTHS,
        )

        lower = result.lower()
        self.assertIn("управление it-проектами полного цикла", lower)
        self.assertIn("финтех", lower)
        self.assertNotIn("многолетний опыт", lower)
        self.assertNotIn("senior/lead", lower)
        self.assertNotIn("30+", result)
        self.assertNotIn("70 человек", result)
        self.assertNotIn("40+", result)
        self.assertNotIn("c-level", lower)

    def test_safe_letter_is_left_unchanged(self):
        original = (
            "Здравствуйте!\n\n"
            "У меня есть опыт управления IT-проектами полного цикла в финтехе. "
            "Вёл проекты от требований и планирования до запуска в production.\n\n"
            "С уважением,\nАлександр Руденко"
        )

        self.assertEqual(
            calibrate_stored_cover_letter(original, STRENGTHS),
            original,
        )

    def test_clean_cover_letter_is_vacancy_aware_and_ai_specific(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "non_negotiable",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "управление сроками, рисками и зависимостями",
                    "candidate_evidence": "project delivery",
                },
                {
                    "criticality": "non_negotiable",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "управление ожиданием внутренних и внешних стейкхолдеров",
                    "candidate_evidence": "stakeholder management",
                },
                {
                    "criticality": "preferred",
                    "category": "other",
                    "match_quality": "partial",
                    "source_text": "Опыт внедрения AI агентов для автоматизации рутины",
                    "candidate_evidence": "implemented ready-made AI solution AutoFAQ for documentation Q&A",
                },
            ]
        }

        result = build_clean_cover_letter(
            vacancy_title="Руководитель ИТ проектов ML AI",
            vacancy_company="ООО Ультиматек",
            vacancy_description="Управление ML/AI проектами и AI агентами.",
            extraction_json=extraction,
        )

        self.assertIn("Руководитель ИТ проектов ML AI", result)
        self.assertIn("ООО Ультиматек", result)
        self.assertIn("управление сроками, рисками и зависимостями", result)
        self.assertIn("стейкхолдеров", result)
        self.assertIn("AutoFAQ", result)
        self.assertIn(AI_PROJECT_URL, result)
        self.assertNotIn("Для этой позиции наиболее релевантны:", result)

    def test_clean_cover_letter_does_not_claim_partial_requirement(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "preferred",
                    "category": "other",
                    "match_quality": "partial",
                    "source_text": "Опыт внедрения AI агентов",
                    "candidate_evidence": "AutoFAQ",
                }
            ]
        }

        result = build_clean_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="Example",
            vacancy_description="Обычные IT-проекты без AI контекста.",
            extraction_json=extraction,
        )

        self.assertNotIn("Опыт внедрения AI агентов", result)

    def test_legacy_cover_letter_is_bound_to_exact_vacancy(self):
        first = build_legacy_vacancy_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="ЗДОРОВ.ру",
            vacancy_description="Автоматизация и интеграции.",
            stored_text=OLD_OVERSOLD,
            strengths=STRENGTHS,
        )
        second = build_legacy_vacancy_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="Ecom.tech",
            vacancy_description="Автоматизация и интеграции.",
            stored_text=OLD_OVERSOLD,
            strengths=STRENGTHS,
        )

        self.assertNotEqual(first, second)
        self.assertIn("Руководитель проектов", first)
        self.assertIn("ЗДОРОВ.ру", first)
        self.assertIn("Ecom.tech", second)
        self.assertTrue(
            is_vacancy_bound_cover_letter(
                first,
                vacancy_title="Руководитель проектов",
                vacancy_company="ЗДОРОВ.ру",
            )
        )
        self.assertFalse(
            is_vacancy_bound_cover_letter(
                first,
                vacancy_title="Руководитель проектов",
                vacancy_company="Ecom.tech",
            )
        )

    def test_generic_calibrated_letter_is_not_vacancy_bound(self):
        generic = calibrate_stored_cover_letter(
            OLD_OVERSOLD,
            STRENGTHS,
        )
        self.assertFalse(
            is_vacancy_bound_cover_letter(
                generic,
                vacancy_title="Руководитель проектов",
                vacancy_company="ЗДОРОВ.ру",
            )
        )

    def test_ai_project_url_survives_recalibration(self):
        original = OLD_OVERSOLD + " " + AI_PROJECT_URL

        result = calibrate_stored_cover_letter(
            original,
            STRENGTHS,
        )

        self.assertIn(AI_PROJECT_URL, result)
        self.assertIn("AI-agent", result)


if __name__ == "__main__":
    unittest.main()
