from __future__ import annotations

import unittest

from sources.tbank import (
    extract_vacancy_links,
    is_inactive,
    is_target_title,
    vacancy_id_from_url,
)


class TBankSourceTests(unittest.TestCase):
    def test_vacancy_id_from_real_it_url(self):
        url = (
            "https://www.tbank.ru/career/it/vacancy/ufa/"
            "prodakt-menedzher-v-komandu-bankovskih-garantij/"
            "4cc5f7ab-21c2-4fd3-b7ab-ab1c28e4b153/"
        )
        self.assertEqual(
            vacancy_id_from_url(url),
            "4cc5f7ab-21c2-4fd3-b7ab-ab1c28e4b153",
        )

    def test_extracts_anchor_and_embedded_links_without_duplicates(self):
        first = (
            "/career/it/vacancy/ufa/product-one/"
            "4cc5f7ab-21c2-4fd3-b7ab-ab1c28e4b153/"
        )
        second = (
            "/career/it/vacancy/moscow/project-two/"
            "076d5548-ec83-4598-a80d-5609155b57ae/"
        )
        page = (
            f'<a href="{first}">one</a>'
            f'<script>window.x={{"url":"{first}","next":"{second}"}}</script>'
        )
        links = extract_vacancy_links(page)
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].startswith("https://www.tbank.ru/career/it/vacancy/"))
        self.assertTrue(links[1].startswith("https://www.tbank.ru/career/it/vacancy/"))

    def test_target_titles(self):
        self.assertTrue(is_target_title("Продакт-менеджер в команду банковских гарантий"))
        self.assertTrue(is_target_title("Technical Product Manager"))
        self.assertTrue(is_target_title("IT Project Manager"))
        self.assertTrue(is_target_title("Руководитель проектов"))
        self.assertFalse(is_target_title("Junior Product Manager"))
        self.assertFalse(is_target_title("Backend Developer"))

    def test_inactive_marker(self):
        self.assertTrue(is_inactive("Набор по вакансии закрыт"))
        self.assertFalse(is_inactive("Откликнуться на вакансию"))


if __name__ == "__main__":
    unittest.main()
