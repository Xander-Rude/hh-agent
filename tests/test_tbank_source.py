from __future__ import annotations

import unittest
from unittest.mock import Mock

from sources.tbank import (
    TBankSource,
    extract_vacancy_links,
    is_inactive,
    is_target_title,
    listing_page_url,
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

    def test_vacancy_id_from_real_back_office_url(self):
        url = (
            "https://www.tbank.ru/career/back-office/vacancy/orsk/"
            "menedzher-proektov-m-a/"
            "7fede700-9e2c-408f-af02-c669fc468224/"
        )
        self.assertEqual(
            vacancy_id_from_url(url),
            "7fede700-9e2c-408f-af02-c669fc468224",
        )

    def test_service_vacancy_is_not_collected(self):
        url = (
            "https://www.tbank.ru/career/service/vacancy/saint-petersburg/"
            "CS_SpecT/43535f5370656354/"
        )
        self.assertIsNone(vacancy_id_from_url(url))

    def test_extracts_anchor_and_embedded_links_without_duplicates(self):
        first = (
            "/career/it/vacancy/ufa/product-one/"
            "4cc5f7ab-21c2-4fd3-b7ab-ab1c28e4b153/"
        )
        second = (
            "/career/back-office/vacancy/moscow/project-two/"
            "076d5548-ec83-4598-a80d-5609155b57ae/"
        )
        page = (
            f'<a href="{first}">one</a>'
            f'<script>window.x={{"url":"{first}","next":"{second}"}}</script>'
        )
        links = extract_vacancy_links(page)
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].startswith("https://www.tbank.ru/career/it/vacancy/"))
        self.assertTrue(
            links[1].startswith("https://www.tbank.ru/career/back-office/vacancy/")
        )

    def test_listing_page_url(self):
        base = "https://www.tbank.ru/career/vacancies/it/"
        self.assertEqual(listing_page_url(base, 1), base)
        self.assertEqual(listing_page_url(base, 2), base + "?page=2")
        self.assertEqual(
            listing_page_url(base + "?city=moscow", 3),
            base + "?city=moscow&page=3",
        )

    def test_target_titles(self):
        self.assertTrue(is_target_title("Продакт-менеджер в команду банковских гарантий"))
        self.assertTrue(is_target_title("Technical Product Manager"))
        self.assertTrue(is_target_title("IT Project Manager"))
        self.assertTrue(is_target_title("Руководитель проектов"))
        self.assertTrue(is_target_title("Менеджер проектов M&A"))
        self.assertTrue(is_target_title("PMO Lead"))
        self.assertTrue(is_target_title("Руководитель проектного офиса"))
        self.assertTrue(is_target_title("Менеджер программ"))
        self.assertFalse(is_target_title("Junior Product Manager"))
        self.assertFalse(is_target_title("Backend Developer"))

    def test_dynamic_discovery_is_always_used_after_static(self):
        source = TBankSource()
        static_links = [
            (
                "https://www.tbank.ru/career/it/vacancy/moscow/project/"
                f"00000000-0000-0000-0000-{index:012d}/"
            )
            for index in range(20)
        ]
        dynamic_links = static_links + [
            (
                "https://www.tbank.ru/career/back-office/vacancy/moscow/project-extra/"
                "ffffffff-ffff-ffff-ffff-ffffffffffff/"
            )
        ]
        source._collect_static_links = Mock(return_value=static_links)
        source._collect_dynamic_links = Mock(return_value=dynamic_links)

        result = source._collect_links(Mock())

        self.assertEqual(result, dynamic_links)
        source._collect_dynamic_links.assert_called_once_with(static_links)

    def test_inactive_marker(self):
        self.assertTrue(is_inactive("Набор по вакансии закрыт"))
        self.assertFalse(is_inactive("Откликнуться на вакансию"))


if __name__ == "__main__":
    unittest.main()
