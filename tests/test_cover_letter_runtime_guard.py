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

    def test_clean_cover_letter_is_human_and_ai_specific(self):
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

        self.assertNotIn("Руководитель ИТ проектов ML AI", result)
        self.assertNotIn("ООО Ультиматек", result)
        self.assertNotIn("управление сроками, рисками и зависимостями", result)
        self.assertIn("сроки, риски, зависимости", result)
        self.assertIn("Синхронизировал бизнес", result)
        self.assertIn("AutoFAQ", result)
        self.assertIn(AI_PROJECT_URL, result)
        self.assertIn("Александр Руденко", result)
        self.assertNotIn("Для этой позиции наиболее релевантны:", result)
        self.assertNotIn("По опыту наиболее близки задачи:", result)

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

    def test_legacy_cover_letter_does_not_echo_vacancy_identity(self):
        result = build_legacy_vacancy_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="ЗДОРОВ.ру",
            vacancy_description="Автоматизация и интеграции.",
            stored_text=OLD_OVERSOLD,
            strengths=STRENGTHS,
        )

        self.assertNotIn("Руководитель проектов", result)
        self.assertNotIn("ЗДОРОВ.ру", result)
        self.assertIn("интеграционные", result.lower())
        self.assertIn("Александр Руденко", result)
        self.assertNotIn("наиболее релевантны", result.lower())

    def test_stray_ai_word_in_description_does_not_add_ai_project(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Управление сроками и рисками",
                    "candidate_evidence": "timelines and risks",
                }
            ]
        }
        result = build_clean_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="Example",
            vacancy_description="Корпоративная платформа. AI упоминается только в общем обзоре технологий.",
            extraction_json=extraction,
        )
        self.assertNotIn(AI_PROJECT_URL, result)

    def test_clean_cover_never_surfaces_raw_internal_evidence(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Технический бэкграунд и понимание архитектуры",
                    "candidate_evidence": (
                        "Technical expertise; experience with highload systems, API, "
                        "infrastructure, and system design context."
                    ),
                }
            ]
        }

        result = build_clean_cover_letter(
            vacancy_title="Руководитель IT проектов",
            vacancy_company="Example",
            vacancy_description="Управление технически сложными IT-проектами.",
            extraction_json=extraction,
        )

        self.assertNotIn("Technical expertise", result)
        self.assertNotIn("Из релевантного опыта:", result)
        self.assertIn("highload-системами", result)
        self.assertIn("системном дизайне", result)

    def test_clean_cover_deprioritizes_hyphenated_tenure(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "non_negotiable",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Опыт руководителем проектов от 3-х лет",
                    "candidate_evidence": "13+ years in IT",
                },
                {
                    "criticality": "non_negotiable",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Ведение проекта на всех этапах разработки",
                    "candidate_evidence": "full lifecycle project delivery",
                },
                {
                    "criticality": "non_negotiable",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Координация кросс-функциональной команды",
                    "candidate_evidence": "teams up to 70",
                },
            ]
        }

        result = build_clean_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="Example",
            vacancy_description="IT delivery.",
            extraction_json=extraction,
        )

        self.assertNotIn("13+ лет", result)
        self.assertIn("полного цикла", result)
        self.assertIn("кросс-функциональные команды", result)

    def test_clean_cover_unknown_requirement_uses_safe_human_fallback(self):
        extraction = {
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "Понимание специфики направления",
                    "candidate_evidence": "Internal English evidence string; do not surface verbatim.",
                }
            ]
        }

        result = build_clean_cover_letter(
            vacancy_title="Руководитель проектов",
            vacancy_company="Example",
            vacancy_description="IT project.",
            extraction_json=extraction,
        )

        self.assertNotIn("Internal English evidence", result)
        self.assertNotIn("Из релевантного опыта:", result)
        self.assertIn("С этим контуром работал", result)

    def test_clean_prefers_validated_human_draft(self):
        draft = (
            "Здравствуйте!\n\n"
            "Мне близки задачи, где нужно собрать несколько потоков разработки в один "
            "предсказуемый delivery и не потерять зависимости между командами. "
            "В похожих проектах я сам держал сроки, риски и синхронизацию бизнеса с IT, "
            "а в МТС сократил Time-to-Market примерно со 100 до 24 дней. "
            "Интересно работать именно с таким сочетанием технической сложности и "
            "ответственности за результат."
        )
        extraction = {
            "cover_letter_draft": draft,
            "requirements": [
                {
                    "criticality": "core",
                    "category": "other",
                    "match_quality": "full",
                    "source_text": "межкомандные зависимости и delivery",
                    "candidate_evidence": "timelines, risks and dependencies",
                }
            ],
        }

        result = build_clean_cover_letter(
            vacancy_title="Senior Project Manager",
            vacancy_company="Example",
            vacancy_description="Несколько потоков разработки и сложные зависимости.",
            extraction_json=extraction,
        )

        self.assertTrue(result.startswith(draft))
        self.assertIn("Александр Руденко", result)
        self.assertNotIn("Example", result)
        self.assertNotIn("Senior Project Manager", result)
        self.assertNotIn("У меня есть релевантный опыт", result)
        self.assertNotIn("С этим контуром", result)

    def test_legacy_prefers_validated_human_draft(self):
        draft = (
            "Здравствуйте!\n\n"
            "Мне близок формат, где проект нужно вести не по статусам, а до реального "
            "результата вместе с бизнесом и технической командой. "
            "Я работал с требованиями, сроками, рисками и ожиданиями заказчиков, "
            "включая сложные интеграционные проекты. "
            "Такой способ работы мне понятен и действительно интересен."
        )

        result = build_legacy_vacancy_cover_letter(
            vacancy_title="Менеджер проектов",
            vacancy_company="Example",
            vacancy_description="Интеграции, заказчики, сроки и риски.",
            stored_text=draft,
            strengths=STRENGTHS,
        )

        self.assertTrue(result.startswith(draft))
        self.assertIn("Александр Руденко", result)
        self.assertNotIn("Example", result)
        self.assertNotIn("Менеджер проектов", result)
        self.assertNotIn("Мой основной профиль", result)

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
