from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from sources.ozon import (
    DetailParser,
    ListingParser,
    canonical_ozon_url,
    is_target_title,
    mirror_vacancy_active,
    ozon_external_id,
)


class OzonSourceTests(unittest.TestCase):
    def test_listing_parser_extracts_overoffer_detail_cards(self) -> None:
        parser = ListingParser()
        parser.feed(
            """
            <a href="/vacancies/100864">
              Technical Project Manager (AI/Fullstack Platform), Ozon Банк
            </a>
            <a href="/docs/privacy">privacy</a>
            """
        )
        self.assertEqual(
            parser.items,
            [
                (
                    "/vacancies/100864",
                    "Technical Project Manager (AI/Fullstack Platform), Ozon Банк",
                )
            ],
        )

    def test_detail_parser_keeps_full_article_and_first_party_url(self) -> None:
        parser = DetailParser()
        parser.feed(
            """
            <html><body>
              <article>
                <h1>Менеджер проектов (Интеграционные сервисы, Ozon Банк)</h1>
                <p>Команда развивает интеграционную платформу.</p>
                <h2>Вам предстоит</h2>
                <p>Вести проекты полного цикла и управлять зависимостями.</p>
                <a href="https://career.ozon.ru/vacancy/137539636">
                  Откликнуться
                </a>
              </article>
            </body></html>
            """
        )
        self.assertEqual(
            parser.title,
            "Менеджер проектов (Интеграционные сервисы, Ozon Банк)",
        )
        self.assertIn("Вести проекты полного цикла", parser.text)
        self.assertEqual(
            parser.external_urls,
            ["https://career.ozon.ru/vacancy/137539636"],
        )

    def test_external_id_supports_career_and_ozon_tech(self) -> None:
        self.assertEqual(
            ozon_external_id(
                "https://career.ozon.ru/vacancy/137539636"
            ),
            "137539636",
        )
        self.assertEqual(
            ozon_external_id(
                "https://career.ozon.ru/vacancy/technical-project-manager-137539636"
            ),
            "137539636",
        )
        self.assertEqual(
            ozon_external_id(
                "https://ozon.tech/vacancies/"
                "b3036118-5956-45de-871a-d53959cb492b/"
            ),
            "b3036118-5956-45de-871a-d53959cb492b",
        )

    def test_only_first_party_ozon_urls_are_accepted(self) -> None:
        self.assertEqual(
            canonical_ozon_url(
                "https://career.ozon.ru/vacancy/137539636?utm=agent"
            ),
            "https://career.ozon.ru/vacancy/137539636",
        )
        self.assertIsNone(
            canonical_ozon_url(
                "https://over-offer.ru/vacancies/100864"
            )
        )

    def test_target_titles_remain_broad(self) -> None:
        for title in (
            "Senior Project Manager, Ozon Банк",
            "Technical Project Manager (AI/Fullstack Platform), Ozon Банк",
            "Ведущий менеджер проектов (AI-трансформация)",
            "Менеджер по продукту, ERP и учетные системы",
            "Руководитель группы продактов Озон Джоб",
            "Delivery Manager",
        ):
            self.assertTrue(is_target_title(title), title)

        for title in (
            "Стажер менеджер проектов",
            "Junior Product Manager",
            "Менеджер по продажам",
            "Go-разработчик",
        ):
            self.assertFalse(is_target_title(title), title)

    def test_mirror_404_ends_query_pagination(self) -> None:
        page_one = Mock()
        page_one.status_code = 200
        page_one.text = (
            '<a href="/vacancies/100864">'
            'Technical Project Manager (AI/Fullstack Platform), Ozon Банк'
            '</a>'
        )
        page_one.raise_for_status = Mock()

        page_two = Mock()
        page_two.status_code = 404
        page_two.text = ""
        page_two.raise_for_status = Mock(
            side_effect=AssertionError("404 must not be raised")
        )

        client = Mock()
        client.get.side_effect = [page_one, page_two]

        from sources.ozon import OzonSource

        with patch(
            "sources.ozon.SEARCH_TERMS",
            ("Ozon project",),
        ):
            result = OzonSource()._collect_mirror_links(client)

        self.assertEqual(
            result,
            [
                (
                    "https://over-offer.ru/vacancies/100864",
                    "Technical Project Manager (AI/Fullstack Platform), Ozon Банк",
                )
            ],
        )
        self.assertEqual(client.get.call_count, 2)

    def test_inactive_flag_is_respected_when_present(self) -> None:
        self.assertFalse(
            mirror_vacancy_active(
                '<script>data:{vacancy:{is_active:false}}</script>'
            )
        )
        self.assertTrue(
            mirror_vacancy_active(
                '<script>data:{vacancy:{is_active:true}}</script>'
            )
        )
        self.assertTrue(mirror_vacancy_active("<article>legacy mirror</article>"))


if __name__ == "__main__":
    unittest.main()
