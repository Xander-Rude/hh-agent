import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.clean_live_guard import (
    CANDIDATE_PROFILE_VERSION,
    CLEAN_ELIGIBLE_ROUTES,
    RECRUITER_RESUME_VERSION,
    clean_eligibility,
)


class CleanLiveGuardTests(unittest.TestCase):
    def test_live_versions_are_bumped_for_final_resume(self) -> None:
        self.assertEqual(
            CANDIDATE_PROFILE_VERSION,
            "candidate-facts-v2-2026-09-26",
        )
        self.assertEqual(
            RECRUITER_RESUME_VERSION,
            "clean-hh-2026-09-26-ats-final",
        )

    def test_only_clean_routes_are_eligible(self) -> None:
        self.assertEqual(
            CLEAN_ELIGIBLE_ROUTES,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    @patch("app.clean_live_guard.current_clean_assessment")
    def test_missing_current_assessment_fails_closed(self, current) -> None:
        current.return_value = None
        result = clean_eligibility(
            object(),
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(
            result.reason,
            "missing_current_clean_assessment",
        )

    @patch("app.clean_live_guard.current_clean_assessment")
    def test_non_clean_route_fails_closed(self, current) -> None:
        current.return_value = SimpleNamespace(
            routing_class="OLD_REVIEW",
        )
        result = clean_eligibility(
            object(),
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "routing_class=OLD_REVIEW")

    @patch("app.clean_live_guard.load_preferences")
    @patch("app.clean_live_guard.current_clean_assessment")
    def test_clean_route_is_vetoed_by_salary_floor(self, current, prefs) -> None:
        current.return_value = SimpleNamespace(routing_class="CLEAN_STRONG")
        prefs.return_value = {
            "salary": 300_000,
            "currency": "RUB",
            "blacklist_companies": [],
        }
        session = SimpleNamespace(
            get=lambda model, vacancy_id: SimpleNamespace(
                salary_from=100_000,
                salary_to=None,
                salary_currency="RUB",
                company="QSOFT",
            )
        )
        result = clean_eligibility(
            session,
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "hard_stop:salary_floor")

    @patch("app.clean_live_guard.load_preferences")
    @patch("app.clean_live_guard.current_clean_assessment")
    def test_clean_route_is_vetoed_by_company_blacklist(self, current, prefs) -> None:
        current.return_value = SimpleNamespace(routing_class="CLEAN_REVIEW")
        prefs.return_value = {
            "salary": 300_000,
            "currency": "RUB",
            "blacklist_companies": ["ГКУ Инфогород"],
        }
        session = SimpleNamespace(
            get=lambda model, vacancy_id: SimpleNamespace(
                salary_from=None,
                salary_to=None,
                salary_currency=None,
                company="ГКУ Инфогород",
            )
        )
        result = clean_eligibility(
            session,
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "hard_stop:blacklist_company")

    @patch("app.clean_live_guard._visible_resume_text")
    @patch("app.clean_live_guard.load_preferences")
    @patch("app.clean_live_guard.current_clean_assessment")
    def test_clean_route_is_vetoed_by_mandatory_developer_role(
        self,
        current,
        prefs,
        visible_resume,
    ) -> None:
        current.return_value = SimpleNamespace(routing_class="CLEAN_STRONG")
        prefs.return_value = {
            "salary": 300_000,
            "currency": "RUB",
            "blacklist_companies": [],
        }
        visible_resume.return_value = (
            "Education: higher education — Менеджмент организации\n"
            "Технический руководитель стрима\n"
            "API and integration design participation"
        )
        session = SimpleNamespace(
            get=lambda model, vacancy_id: SimpleNamespace(
                salary_from=None,
                salary_to=None,
                salary_currency=None,
                company="АО Р7",
                title="Администратор проектов",
                description=(
                    "Требования: Технический бэкграунд в роли разработчика."
                ),
            )
        )
        result = clean_eligibility(
            session,
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "hard_stop:mandatory_hands_on")

    @patch("app.clean_live_guard.load_preferences")
    @patch("app.clean_live_guard.current_clean_assessment")
    def test_old_clean_route_is_vetoed_by_service_operations(
        self,
        current,
        prefs,
    ) -> None:
        current.return_value = SimpleNamespace(routing_class="CLEAN_STRONG")
        prefs.return_value = {
            "salary": 300_000,
            "currency": "RUB",
            "blacklist_companies": [],
        }
        session = SimpleNamespace(
            get=lambda model, vacancy_id: SimpleNamespace(
                salary_from=None,
                salary_to=None,
                salary_currency=None,
                company="ООО Мароп",
                title="Руководитель ИТ-проектов",
                description=(
                    "Организация и управление работой эксплуатационных служб. "
                    "Контроль SLA/KPI. Управление эскалацией инцидентов и проблем. "
                    "Формирование процессов эксплуатации и технической поддержки."
                ),
            )
        )
        result = clean_eligibility(
            session,
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(
            result.reason,
            "hard_stop:role_family_noncore",
        )

    @patch("app.clean_live_guard._visible_resume_text")
    @patch("app.clean_live_guard.load_preferences")
    @patch("app.clean_live_guard.current_clean_assessment")
    def test_old_clean_route_is_vetoed_by_missing_bitrix_configuration(
        self,
        current,
        prefs,
        visible_resume,
    ) -> None:
        current.return_value = SimpleNamespace(routing_class="CLEAN_STRONG")
        prefs.return_value = {
            "salary": 300_000,
            "currency": "RUB",
            "blacklist_companies": [],
        }
        visible_resume.return_value = (
            "Senior IT Project Manager. Jira, Confluence, API integrations."
        )
        session = SimpleNamespace(
            get=lambda model, vacancy_id: SimpleNamespace(
                salary_from=None,
                salary_to=None,
                salary_currency=None,
                company="МТС",
                title="Руководитель направления автоматизации Битрикс24",
                description=(
                    "Требования: опыт настройки Битрикс24: CRM, "
                    "бизнес-процессы, автоматизации, роботы и триггеры."
                ),
            )
        )
        result = clean_eligibility(
            session,
            123,
            context=SimpleNamespace(),
        )
        self.assertFalse(result.eligible)
        self.assertEqual(
            result.reason,
            "hard_stop:mandatory_exact_stack",
        )

    @patch("app.clean_live_guard.current_clean_assessment")
    def test_clean_review_is_eligible(self, current) -> None:
        assessment = SimpleNamespace(
            routing_class="CLEAN_REVIEW",
        )
        current.return_value = assessment
        result = clean_eligibility(
            object(),
            123,
            context=SimpleNamespace(),
        )
        self.assertTrue(result.eligible)
        self.assertIs(result.assessment, assessment)


if __name__ == "__main__":
    unittest.main()
