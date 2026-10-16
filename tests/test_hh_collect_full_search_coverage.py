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
                patch.object(hh_collect, "SEARCH_MAX_DEPTH_PAGES", 8),
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "clean"),
            ):
                query = "Руководитель проектов"
                other = "Program Manager"
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 1, 2])
                self.assertEqual(hh_collect.save_search_cursor(query, 3), 3)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 3, 4])
                self.assertEqual(hh_collect.search_page_numbers(other), [0, 1, 2])
                self.assertEqual(hh_collect.save_search_cursor(query, 7), 7)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 7])
                self.assertEqual(hh_collect.save_search_cursor(query, 8), 1)
                self.assertEqual(hh_collect.search_page_numbers(query), [0, 1, 2])
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

    def test_old_visits_each_recommendation_feed_fresh_page_first(self):
        urls = [
            "https://hh.ru/search/vacancy?resume=old-resume-1",
            "https://hh.ru/search/vacancy?resume=old-resume-2",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "old"),
                patch.object(hh_collect, "MAX_RECOMMENDATION_PAGES", 8),
                patch.object(
                    hh_collect,
                    "RECOMMENDATION_CURSOR_STATE_PATH",
                    Path(tmp) / "recommendations.json",
                ),
            ):
                plan = hh_collect.recommendation_visit_plan(urls)
        self.assertEqual(plan[0], (1, urls[0], 0, 1))
        self.assertEqual(plan[1], (2, urls[1], 0, 1))
        self.assertEqual(len(plan), 16)
        self.assertEqual(len(set((feed, page) for feed, _, page, _ in plan)), 16)

    def test_clean_keeps_three_fresh_pages_before_deep_crawl(self):
        urls = [
            "https://hh.ru/search/vacancy?resume=clean-1",
            "https://hh.ru/search/vacancy?resume=clean-2",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "clean"),
                patch.object(hh_collect, "MAX_RECOMMENDATION_PAGES", 20),
                patch.object(hh_collect, "CLEAN_RECOMMENDATION_FRESH_PAGES", 3),
                patch.object(hh_collect, "CLEAN_RECOMMENDATION_BACKLOG_PAGES_PER_RUN", 4),
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
