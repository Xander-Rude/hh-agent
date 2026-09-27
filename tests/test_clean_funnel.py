import unittest

from app.clean_funnel import (
    _primary_suppression_reason,
    _source_bucket,
    format_clean_funnel_lines,
)


class CleanFunnelTests(unittest.TestCase):
    def test_source_bucket_splits_recommendation_search_and_both(self) -> None:
        self.assertEqual(
            _source_bucket({"recommendation"}),
            "recommendation",
        )
        self.assertEqual(
            _source_bucket({"search"}),
            "search",
        )
        self.assertEqual(
            _source_bucket({"recommendation", "search"}),
            "both",
        )

    def test_hard_stop_is_more_specific_than_route_reason(self) -> None:
        self.assertEqual(
            _primary_suppression_reason(
                eligibility_reason="routing_class=OLD_REVIEW",
                hard_stops=["mandatory_exact_stack"],
            ),
            "hard_stop:mandatory_exact_stack",
        )

    def test_live_guard_hard_stop_wins_over_materialized_stops(self) -> None:
        self.assertEqual(
            _primary_suppression_reason(
                eligibility_reason="hard_stop:salary_floor",
                hard_stops=["mandatory_exact_stack"],
            ),
            "hard_stop:salary_floor",
        )

    def test_tech_lines_expose_age_regression_metric(self) -> None:
        lines = format_clean_funnel_lines(
            {
                "discovered": 96,
                "discovered_by_source": {
                    "recommendation": 20,
                    "search": 60,
                    "both": 16,
                },
                "assessed": 96,
                "pending_assessment": 0,
                "eligible": 20,
                "suppressed": 76,
                "routes": {
                    "CLEAN_STRONG": 18,
                    "CLEAN_REVIEW": 2,
                    "SKIP": 50,
                    "OLD_REVIEW": 26,
                },
                "surfaced": 10,
                "skipped": 4,
                "applied": 2,
                "suppressed_by_reason": {
                    "routing_class=SKIP": 50,
                    "routing_class=OLD_REVIEW": 26,
                },
                "today": {
                    "discovered": 96,
                    "global_age_suppressed": 0,
                    "legacy_global_age_mismatch": 64,
                },
            }
        )

        rendered = "\n".join(lines)
        self.assertIn("rec 20", rendered)
        self.assertIn("search 60", rendered)
        self.assertIn("both 16", rendered)
        self.assertIn("age_suppressed 0", rendered)
        self.assertIn("legacy_age_mismatch 64", rendered)


if __name__ == "__main__":
    unittest.main()
