from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from resume_raise_schedule import (
    next_raise_at_from_text,
    retry_at,
    should_run_for_due_at,
    to_iso,
)


TZ = timezone(timedelta(hours=3))


class ResumeRaiseScheduleTests(unittest.TestCase):
    def test_picks_earliest_future_time(self) -> None:
        now = datetime(2026, 9, 10, 18, 23, tzinfo=TZ)
        text = "Поднять в 19:26\nПоднять в 19:25\nПоднять в 19:25"

        due = next_raise_at_from_text(text, now=now)

        self.assertEqual(due, datetime(2026, 9, 10, 19, 25, tzinfo=TZ))

    def test_rolls_clock_time_over_midnight(self) -> None:
        now = datetime(2026, 9, 10, 23, 50, tzinfo=TZ)

        due = next_raise_at_from_text("Поднять в 00:05", now=now)

        self.assertEqual(due, datetime(2026, 9, 11, 0, 5, tzinfo=TZ))

    def test_recently_passed_time_is_due_now(self) -> None:
        now = datetime(2026, 9, 10, 19, 27, tzinfo=TZ)

        due = next_raise_at_from_text("Поднять в 19:25", now=now)

        self.assertEqual(due, now)

    def test_far_past_clock_time_is_tomorrow(self) -> None:
        now = datetime(2026, 9, 10, 19, 0, tzinfo=TZ)

        due = next_raise_at_from_text("Поднять в 08:00", now=now)

        self.assertEqual(due, datetime(2026, 9, 11, 8, 0, tzinfo=TZ))

    def test_missing_due_state_fails_open(self) -> None:
        now = datetime(2026, 9, 10, 18, 0, tzinfo=TZ)

        self.assertTrue(should_run_for_due_at(None, now=now))
        self.assertTrue(should_run_for_due_at("garbage", now=now))

    def test_future_due_state_skips_expensive_worker(self) -> None:
        now = datetime(2026, 9, 10, 18, 0, tzinfo=TZ)
        due = datetime(2026, 9, 10, 19, 25, tzinfo=TZ)

        self.assertFalse(should_run_for_due_at(to_iso(due), now=now))
        self.assertTrue(
            should_run_for_due_at(
                to_iso(due),
                now=datetime(2026, 9, 10, 19, 24, 45, tzinfo=TZ),
            )
        )

    def test_retry_at_has_minimum_one_minute(self) -> None:
        now = datetime(2026, 9, 10, 18, 0, tzinfo=TZ)

        self.assertEqual(retry_at(0, now=now), now + timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
