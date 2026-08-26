import unittest

from app.hard_filters import (
    apply_hard_filters,
    check_blacklist_company,
    check_blacklist_words,
    check_salary,
    check_unwanted_domains,
)


class HardFilterTests(unittest.TestCase):
    def test_salary_below_minimum_is_rejected(self):
        result = check_salary(
            salary_from=200000,
            salary_to=250000,
            salary_currency="RUB",
            preferences={"salary": {"minimum": 350000, "currency": "RUB"}},
        )
        self.assertFalse(result.passed)
        self.assertIn("ниже минимума", result.reason)

    def test_missing_salary_is_not_rejected(self):
        result = check_salary(
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            preferences={"salary": {"minimum": 350000, "currency": "RUB"}},
        )
        self.assertTrue(result.passed)

    def test_foreign_currency_salary_is_not_hard_rejected(self):
        result = check_salary(
            salary_from=3000,
            salary_to=4000,
            salary_currency="USD",
            preferences={"salary": {"minimum": 350000, "currency": "RUB"}},
        )
        self.assertTrue(result.passed)

    def test_blacklist_company_matches_case_insensitively(self):
        result = check_blacklist_company(
            company="Example Gambling LLC",
            preferences={"blacklist_companies": ["gambling llc"]},
        )
        self.assertFalse(result.passed)

    def test_blacklist_word_in_description_alone_does_not_reject(self):
        result = check_blacklist_words(
            title="Senior Project Manager",
            description="Работа с developer-командами и аналитиками",
            preferences={"blacklist_words": ["developer"]},
        )
        self.assertTrue(result.passed)

    def test_blacklist_word_in_title_rejects(self):
        result = check_blacklist_words(
            title="Developer Project Manager",
            description="",
            preferences={"blacklist_words": ["developer"]},
        )
        self.assertFalse(result.passed)

    def test_unwanted_domain_in_description_alone_does_not_reject(self):
        result = check_unwanted_domains(
            title="Senior Product Manager",
            description="Опыт интеграций с crypto-провайдерами будет плюсом",
            preferences={"unwanted_domains": ["crypto"]},
        )
        self.assertTrue(result.passed)

    def test_unwanted_domain_in_title_rejects(self):
        result = check_unwanted_domains(
            title="Senior Product Manager Crypto",
            description="",
            preferences={"unwanted_domains": ["crypto"]},
        )
        self.assertFalse(result.passed)

    def test_apply_hard_filters_accepts_valid_role(self):
        result = apply_hard_filters(
            title="Senior Project Manager",
            company="Example",
            description="",
            salary_from=400000,
            salary_to=None,
            salary_currency="RUB",
            preferences={
                "salary": {"minimum": 350000, "currency": "RUB"},
                "blacklist_companies": [],
                "blacklist_words": [],
                "unwanted_domains": [],
            },
        )
        self.assertTrue(result.passed)

    def test_apply_hard_filters_rejects_blocked_role_first(self):
        result = apply_hard_filters(
            title="Python Developer",
            company="Example",
            description="",
            salary_from=500000,
            salary_to=None,
            salary_currency="RUB",
            preferences={
                "salary": {"minimum": 350000, "currency": "RUB"},
                "blacklist_companies": [],
                "blacklist_words": [],
                "unwanted_domains": [],
            },
        )
        self.assertFalse(result.passed)
        self.assertIn("Неподходящая роль", result.reason)


if __name__ == "__main__":
    unittest.main()
