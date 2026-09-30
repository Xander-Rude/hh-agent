import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import hh_collect


class HHCollectTrafficGuardTests(unittest.TestCase):
    def test_response_history_cache_respects_ttl(self) -> None:
        now = datetime(2026, 9, 19, 18, 0, 0)
        fresh = SimpleNamespace(
            hh_response_checked_at=now - timedelta(hours=11, minutes=59)
        )
        stale = SimpleNamespace(
            hh_response_checked_at=now - timedelta(hours=12, minutes=1)
        )
        never_checked = SimpleNamespace(hh_response_checked_at=None)

        with (
            patch.object(hh_collect, "RESPONSE_CHECK_TTL_MIN_HOURS", 12),
            patch.object(hh_collect, "RESPONSE_CHECK_TTL_MAX_HOURS", 12),
        ):
            self.assertTrue(
                hh_collect.response_check_cache_is_fresh(fresh, now=now)
            )
            self.assertFalse(
                hh_collect.response_check_cache_is_fresh(stale, now=now)
            )
            self.assertFalse(
                hh_collect.response_check_cache_is_fresh(
                    never_checked,
                    now=now,
                )
            )

    def test_response_history_ttl_is_staggered_without_count_limit(self) -> None:
        first = SimpleNamespace(hh_id="137095431")
        second = SimpleNamespace(hh_id="136366212")

        with (
            patch.object(hh_collect, "RESPONSE_CHECK_TTL_MIN_HOURS", 12),
            patch.object(hh_collect, "RESPONSE_CHECK_TTL_MAX_HOURS", 24),
        ):
            first_ttl = hh_collect.response_check_ttl_hours(first)
            second_ttl = hh_collect.response_check_ttl_hours(second)

        self.assertGreaterEqual(first_ttl, 12)
        self.assertLessEqual(first_ttl, 24)
        self.assertGreaterEqual(second_ttl, 12)
        self.assertLessEqual(second_ttl, 24)
        self.assertNotEqual(first_ttl, second_ttl)

    def test_new_vacancies_are_prioritized_without_hh_requests(self) -> None:
        class ScalarResult:
            def all(self):
                return ["100", "300"]

        class FakeSession:
            def scalars(self, statement):
                return ScalarResult()

            def close(self) -> None:
                pass

        urls = [
            "https://hh.ru/vacancy/100",
            "https://hh.ru/vacancy/200",
            "https://hh.ru/vacancy/300",
            "https://hh.ru/vacancy/400",
        ]

        with patch.object(
            hh_collect,
            "SessionLocal",
            return_value=FakeSession(),
        ):
            ordered = hh_collect.prioritize_new_vacancy_urls(urls)

        self.assertEqual(
            ordered,
            [
                "https://hh.ru/vacancy/200",
                "https://hh.ru/vacancy/400",
                "https://hh.ru/vacancy/100",
                "https://hh.ru/vacancy/300",
            ],
        )

    def test_transient_captcha_redirect_does_not_persist_pause(self) -> None:
        page = SimpleNamespace(goto=MagicMock())
        with (
            patch.object(
                hh_collect,
                "detect_hh_block",
                side_effect=[
                    "HH открыл служебную страницу: https://hh.ru/account/captcha",
                    None,
                ],
            ),
            patch.object(hh_collect.time, "sleep"),
            patch.object(hh_collect, "activate_hh_cooldown") as cooldown,
            patch.object(hh_collect, "pause_for_captcha") as pause,
            patch.object(hh_collect, "touch_watchdog"),
        ):
            hh_collect.goto_or_stop(
                page,
                "https://hh.ru/vacancy/136354158",
                context_label="test vacancy",
            )

        self.assertEqual(page.goto.call_count, 2)
        cooldown.assert_not_called()
        pause.assert_not_called()

    def test_repeated_captcha_redirect_persists_pause(self) -> None:
        page = SimpleNamespace(goto=MagicMock())
        reason = "HH открыл служебную страницу: https://hh.ru/account/captcha"
        with (
            patch.object(
                hh_collect,
                "detect_hh_block",
                side_effect=[reason, reason],
            ),
            patch.object(hh_collect.time, "sleep"),
            patch.object(
                hh_collect,
                "activate_hh_cooldown",
                return_value=datetime.now(UTC) + timedelta(hours=4),
            ) as cooldown,
            patch.object(hh_collect, "pause_for_captcha") as pause,
            patch.object(hh_collect, "touch_watchdog"),
        ):
            with self.assertRaises(hh_collect.CollectorFatalError):
                hh_collect.goto_or_stop(
                    page,
                    "https://hh.ru/vacancy/136354158",
                    context_label="test vacancy",
                )

        self.assertEqual(page.goto.call_count, 2)
        cooldown.assert_called_once_with(reason)
        pause.assert_called_once_with(hh_collect.COLLECT_ACCOUNT_KEY, reason)

    def test_captcha_cooldown_survives_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "hh_collect_cooldown.json"
            before = datetime.now(UTC)

            with (
                patch.object(hh_collect, "COOLDOWN_STATE_PATH", state_path),
                patch.object(hh_collect, "CAPTCHA_COOLDOWN_HOURS", 4),
            ):
                until = hh_collect.activate_hh_cooldown("captcha")
                remaining = hh_collect.hh_cooldown_remaining_seconds(
                    now=before
                )

                self.assertTrue(state_path.exists())
                self.assertGreaterEqual(remaining, 4 * 60 * 60)
                self.assertLess(remaining, 4 * 60 * 60 + 10)
                self.assertGreater(until, before)

    def test_expired_cooldown_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "hh_collect_cooldown.json"
            now = datetime.now(UTC)
            state_path.write_text(
                json.dumps(
                    {
                        "until": (now - timedelta(minutes=1)).isoformat(),
                        "reason": "captcha",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                hh_collect,
                "COOLDOWN_STATE_PATH",
                state_path,
            ):
                remaining = hh_collect.hh_cooldown_remaining_seconds(now=now)

            self.assertEqual(remaining, 0.0)
            self.assertFalse(state_path.exists())

    def test_search_navigation_retry_recovers(self) -> None:
        expected = ["https://hh.ru/vacancy/123"]
        transient = hh_collect.CollectorNavigationError("temporary timeout")

        with (
            patch.object(hh_collect, "NAVIGATION_RETRY_ATTEMPTS", 3),
            patch.object(hh_collect, "NAVIGATION_RETRY_DELAY_SECONDS", 0),
            patch.object(
                hh_collect,
                "collect_links",
                side_effect=[transient, expected],
            ) as collect_links,
            patch.object(hh_collect, "sleep_with_jitter") as sleep,
        ):
            result = hh_collect.collect_links_with_navigation_retry(
                page=object(),
                search_url="https://hh.ru/search/vacancy?text=PM",
                context_label="test search",
            )

        self.assertEqual(result, expected)
        self.assertEqual(collect_links.call_count, 2)
        sleep.assert_called_once()

    def test_search_navigation_retry_skips_after_limit(self) -> None:
        transient = hh_collect.CollectorNavigationError("temporary timeout")

        with (
            patch.object(hh_collect, "NAVIGATION_RETRY_ATTEMPTS", 3),
            patch.object(hh_collect, "NAVIGATION_RETRY_DELAY_SECONDS", 0),
            patch.object(
                hh_collect,
                "collect_links",
                side_effect=transient,
            ) as collect_links,
            patch.object(hh_collect, "sleep_with_jitter"),
        ):
            result = hh_collect.collect_links_with_navigation_retry(
                page=object(),
                search_url="https://hh.ru/search/vacancy?text=PM",
                context_label="test search",
            )

        self.assertIsNone(result)
        self.assertEqual(collect_links.call_count, 3)

    def test_search_navigation_retry_keeps_antibot_fatal(self) -> None:
        fatal = hh_collect.CollectorFatalError("captcha")

        with (
            patch.object(hh_collect, "NAVIGATION_RETRY_ATTEMPTS", 3),
            patch.object(
                hh_collect,
                "collect_links",
                side_effect=fatal,
            ) as collect_links,
        ):
            with self.assertRaises(hh_collect.CollectorFatalError):
                hh_collect.collect_links_with_navigation_retry(
                    page=object(),
                    search_url="https://hh.ru/search/vacancy?text=PM",
                    context_label="test search",
                )

        self.assertEqual(collect_links.call_count, 1)


    def test_old_recommendation_cursor_resumes_after_fresh_page(self) -> None:
        feed = (
            "https://hh.ru/search/vacancy?"
            "resume=test-resume&from=recommendations&page=0"
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cursor.json"
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "old"),
                patch.object(hh_collect, "MAX_RECOMMENDATION_PAGES", 10),
                patch.object(
                    hh_collect,
                    "RECOMMENDATION_CURSOR_STATE_PATH",
                    state_path,
                ),
            ):
                hh_collect.save_old_recommendation_cursor(feed, 8)
                pages = hh_collect.recommendation_page_numbers(feed)

        self.assertEqual(pages[:4], [0, 8, 9, 1])
        self.assertEqual(len(pages), 10)
        self.assertEqual(len(set(pages)), 10)

    def test_old_recommendation_cursor_persists_and_wraps(self) -> None:
        feed = (
            "https://hh.ru/search/vacancy?"
            "resume=test-resume&from=recommendations"
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cursor.json"
            with (
                patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "old"),
                patch.object(hh_collect, "MAX_RECOMMENDATION_PAGES", 10),
                patch.object(
                    hh_collect,
                    "RECOMMENDATION_CURSOR_STATE_PATH",
                    state_path,
                ),
            ):
                saved = hh_collect.save_old_recommendation_cursor(feed, 9)
                self.assertEqual(saved, 9)
                self.assertEqual(
                    hh_collect.old_recommendation_cursor_page(feed),
                    9,
                )

                wrapped = hh_collect.save_old_recommendation_cursor(feed, 10)
                self.assertEqual(wrapped, 1)
                self.assertEqual(
                    hh_collect.old_recommendation_cursor_page(feed),
                    1,
                )

                payload = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["account_key"], "old")
        self.assertEqual(len(payload["feeds"]), 1)

    def test_clean_recommendations_ignore_old_cursor_logic(self) -> None:
        feed = "https://hh.ru/search/vacancy?resume=test-resume"
        with (
            patch.object(hh_collect, "COLLECT_ACCOUNT_KEY", "clean"),
            patch.object(hh_collect, "MAX_RECOMMENDATION_PAGES", 3),
        ):
            self.assertEqual(
                hh_collect.recommendation_page_numbers(feed),
                [0, 1, 2],
            )


if __name__ == "__main__":
    unittest.main()
