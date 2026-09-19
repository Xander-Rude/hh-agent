import unittest

from app.cover_letter_runtime import (
    AI_PROJECT_URL,
    calibrate_stored_cover_letter,
    is_oversold_cover_letter,
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
