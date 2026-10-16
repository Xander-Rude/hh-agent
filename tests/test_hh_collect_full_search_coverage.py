import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import hh_collect
from hh_collect_optimized import _is_fallback_search_url


class HHCollectorFullCoverageTests(unittest.TestCase):
    def test_search_has_no_age_restriction_even_for_month_old_postings(self):
        url = hh_collect.build_search_url("Senior Project Manager", 0)
        params = parse_qs(urlparse(url).query)
        self.assertNotIn("period", params)
        self.assertEqual(params["text"], ["Senior Project Manager"])
        self.assertEqual(params["search_field"], ["name"])
        self.assertEqual(params["area"], ["1"])
        self.assertEqual(params["page"], ["0"])
        self.assertTrue(_is_fallback_search_url(url))

    def test_role_gate_does_not_require_age_param(self):
        self.assertTrue(
            _is_fallback_search_url(
                "https://hh.ru/search/vacancy?text=Delivery+Manager&search_field=name"
            )
        )
        self.assertFalse(
            _is_fallback_search_url(
                "https://hh.ru/search/vacancy?text=Delivery+Manager&search_field=description"
            )
        )

    def test_deep_search_resumes_persists_and_wraps(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "search.json"
            with (
                patch.object(hh_collect, "SEARCH_CURSOR_STATE_PATH", state_path),
                patch.object(hh_collect, "SEARCH_DEEP_PAGES_PER_RUN", 2),
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "clean"),
            ):
                query = "Руководитель проектов"
                other = "Program Manager"
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 1, 2])
                self.assertEqual(hh_collect.save_search_cursor(query, 3), 3)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 3, 4])
                self.assertEqual(hh_collect.search_page_numbers(other), [0, 1, 2])
                self.assertEqual(hh_collect.save_search_cursor(query, 7), 7)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 7, 8])
                self.assertEqual(hh_collect.save_search_cursor(query, 8), 8)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 8, 9])
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["account_key"], "clean")
                self.assertEqual(len(payload["queries"]), 1)

    def test_search_query_order_rotates_after_partial_run(self):
        queries = ["Руководитель проектов", "Program Manager", "PMO Lead"]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                hh_collect,
                "SEARCH_CURSOR_STATE_PATH",
                Path(tmp) / "search.json",
            ):
                self.assertEqual(
                    hh_collect.search_query_visit_plan(queries),
                    [(1, queries[0]), (2, queries[1]), (3, queries[2])],
                )
                hh_collect.advance_search_query_cursor(1, queries)
                self.assertEqual(
                    hh_collect.search_query_visit_plan(queries),
                    [(2, queries[1]), (3, queries[2]), (1, queries[0])],
                )
                hh_collect.advance_search_query_cursor(3, queries)
                self.assertEqual(
                    hh_collect.search_query_visit_plan(queries)[0],
                    (1, queries[0]),
                )
                self.assertEqual(
                    hh_collect.search_query_visit_plan(
                        ["Senior Project Manager", "PMO Lead"]
                    )[0],
                    (1, "Senior Project Manager"),
                )

    def test_both_accounts_share_recommendation_policy_without_quota(self):
        self.assertEqual(hh_collect.RECOMMENDATION_FRESH_PAGES, 3)
        self.assertEqual(hh_collect.RECOMMENDATION_DEEP_PAGES_PER_RUN, 10)
        self.assertFalse(hasattr(hh_collect, "MAX_RECOMMENDATION_PAGES"))
        self.assertFalse(hasattr(hh_collect, "MAX_NEW_VACANCIES_TOTAL"))
        self.assertFalse(hasattr(hh_collect, "OLD_RECOMMENDATION_NEW_PER_RUN"))
        self.assertFalse(hasattr(hh_collect, "SEARCH_MAX_DEPTH_PAGES"))

    def test_cursor_can_continue_beyond_page_100(self):
        feed = "https://hh.ru/search/vacancy?resume=test"
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(hh_collect, "RECOMMENDATION_CURSOR_STATE_PATH", Path(tmp) / "rec.json"),
                patch.object(hh_collect, "RECOMMENDATION_FRESH_PAGES", 3),
                patch.object(hh_collect, "RECOMMENDATION_DEEP_PAGES_PER_RUN", 3),
            ):
                hh_collect.save_recommendation_cursor(feed, 100)
                self.assertEqual(hh_collect.recommendation_page_numbers(feed), [0, 1, 2, 100, 101, 102])

    def test_exhausted_time_budget_returns_partial_page_for_retry(self):
        with (
            patch.object(hh_collect, "prioritize_new_vacancy_urls", return_value=["https://hh.ru/vacancy/123"]),
            patch.object(hh_collect.time, "monotonic", return_value=1200),
        ):
            score, incomplete = hh_collect.process_vacancy_links(
                page=object(),
                links=["https://hh.ru/vacancy/123"],
                source_label="HH_RECOMMENDATION",
                apply_role_gate=False,
                seen_this_run=set(),
                saved_total=0,
                deadline_monotonic=1000,
            )
        self.assertEqual(score, 0)
        self.assertTrue(incomplete)

    def test_old_visits_each_recommendation_feed_fresh_page_first(self):
        urls = [
            "https://hh.ru/search/vacancy?resume=old-resume-1",
            "https://hh.ru/search/vacancy?resume=old-resume-2",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "old"),
                patch.object(hh_collect, "RECOMMENDATION_FRESH_PAGES", 3),
                patch.object(hh_collect, "RECOMMENDATION_DEEP_PAGES_PER_RUN", 4),
                patch.object(
                    hh_collect,
                    "RECOMMENDATION_CURSOR_STATE_PATH",
                    Path(tmp) / "recommendations.json",
                ),
            ):
                plan = hh_collect.recommendation_visit_plan(urls)
        self.assertEqual(plan[0], (1, urls[0], 0, 3))
        self.assertEqual(plan[3], (2, urls[1], 0, 3))
        self.assertEqual(len(plan), 14)
        self.assertEqual(len(set((feed, page) for feed, _, page, _ in plan)), 14)
        self.assertEqual(
            [(feed, page) for feed, _, page, _ in plan[6:10]],
            [(1, 3), (2, 3), (1, 4), (2, 4)],
        )

    def test_clean_keeps_three_fresh_pages_before_deep_crawl(self):
        urls = [
            "https://hh.ru/search/vacancy?resume=clean-1",
            "https://hh.ru/search/vacancy?resume=clean-2",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "clean"),
                patch.object(hh_collect, "RECOMMENDATION_FRESH_PAGES", 3),
                patch.object(hh_collect, "RECOMMENDATION_DEEP_PAGES_PER_RUN", 4),
                patch.object(
                    hh_collect,
                    "RECOMMENDATION_CURSOR_STATE_PATH",
                    Path(tmp) / "recommendations.json",
                ),
            ):
                plan = hh_collect.recommendation_visit_plan(urls)
        self.assertEqual(
            [(feed, page) for feed, _, page, _ in plan[:6]],
            [(1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
        )
        self.assertEqual(len(plan), 14)


if __name__ == "__main__":
    unittest.main()
