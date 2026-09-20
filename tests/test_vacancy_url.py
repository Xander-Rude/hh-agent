from __future__ import annotations

import unittest

from app.vacancy_url import canonicalize_url


class VacancyUrlTests(unittest.TestCase):
    def test_percent_encodes_spaces_in_path(self):
        raw = (
            "https://www.tbank.ru/career/it/vacancy/shakhty/IT Team Lead/"
            "3d187fff-433e-4533-8840-970d220c4ca9/"
        )
        self.assertEqual(
            canonicalize_url(raw),
            "https://www.tbank.ru/career/it/vacancy/shakhty/"
            "IT%20Team%20Lead/3d187fff-433e-4533-8840-970d220c4ca9/",
        )

    def test_does_not_double_encode_existing_escape(self):
        encoded = (
            "https://www.tbank.ru/career/it/vacancy/shakhty/IT%20Team%20Lead/"
            "3d187fff-433e-4533-8840-970d220c4ca9/"
        )
        self.assertEqual(canonicalize_url(encoded), encoded)


if __name__ == "__main__":
    unittest.main()
