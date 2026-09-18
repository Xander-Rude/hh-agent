import unittest

from hh_collect_policy import (
    is_obvious_non_target_title,
    is_target_title,
    prioritize_search_queries,
)


class HHCollectOptimizedTests(unittest.TestCase):
    def test_obvious_non_target_titles_are_rejected(self) -> None:
        titles = [
            "Руководитель направления бренд-маркетинга",
            "Руководитель направления продаж",
            "HR Business Partner (IT, Админ, Аппарат совета директоров)",
            "Начальник отдела образовательных программ ДПО",
            "Директор по закупкам",
            "Системный администратор / Помощник IT-директора",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(is_obvious_non_target_title(title))
                self.assertFalse(is_target_title(title))

    def test_ambiguous_and_senior_titles_stay_eligible(self) -> None:
        titles = [
            "Руководитель направления",
            "Руководитель направления реализации (платформа Яндекс.Смена)",
            "Руководитель направления маркетплейсов",
            "Руководитель департамента",
            "Заместитель директора",
            "Директор",
            "Head of Platform",
            "CTO",
            "CIO",
            "Директор по информационным технологиям",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(is_obvious_non_target_title(title))
                self.assertTrue(is_target_title(title))

    def test_queries_prioritized_without_losing_coverage(self) -> None:
        queries = [
            "Руководитель направления",
            "Project Manager",
            "CTO",
            "IT Business Partner",
            "Project Manager",
            "CIO",
        ]
        result = prioritize_search_queries(queries)
        self.assertEqual(
            set(result),
            {
                "Руководитель направления",
                "Project Manager",
                "CTO",
                "IT Business Partner",
                "CIO",
            },
        )
        self.assertEqual(result[0], "Project Manager")
        self.assertLess(result.index("Project Manager"), result.index("CTO"))
        self.assertLess(result.index("Project Manager"), result.index("CIO"))
        self.assertEqual(result.count("Project Manager"), 1)


if __name__ == "__main__":
    unittest.main()
