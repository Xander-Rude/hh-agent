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
