import unittest
from types import SimpleNamespace

from telegram_cover_letter_output_patch import build_vacancy_header


class TelegramCoverLetterOutputTests(unittest.TestCase):
    def test_header_contains_title_and_company_only(self):
        result = SimpleNamespace(
            title="Заместитель технического директора",
            company="Иви",
            used_cached_evaluation=False,
        )

        self.assertEqual(
            build_vacancy_header(result),
            "✉️ Заместитель технического директора\nИви",
        )

    def test_cache_note_stays_in_header(self):
        result = SimpleNamespace(
            title="Project Manager",
            company=None,
            used_cached_evaluation=True,
        )

        self.assertEqual(
            build_vacancy_header(result),
            "✉️ Project Manager\n\n⚡ Использована уже рассчитанная оценка из базы.",
        )


if __name__ == "__main__":
    unittest.main()
