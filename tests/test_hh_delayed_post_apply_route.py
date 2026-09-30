import unittest
from unittest.mock import MagicMock, patch

import apply_worker as worker


class DelayedPostApplyRouteTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()

    def test_waits_for_delayed_already_applied_state(self):
        with (
            patch.object(worker, "already_applied", side_effect=[False, False, True]),
            patch.object(worker, "find_post_apply_cover_letter_trigger", return_value=None),
            patch.object(worker, "find_visible", return_value=None),
            patch.object(worker, "detect_manual_required", return_value=None),
        ):
            self.assertTrue(
                worker.wait_for_post_apply_transition(
                    self.page,
                    attempts=5,
                    delay_ms=1,
                )
            )

        self.assertEqual(self.page.wait_for_timeout.call_count, 2)

    def test_waits_for_delayed_post_apply_letter_trigger(self):
        trigger = object()
        with (
            patch.object(worker, "already_applied", return_value=False),
            patch.object(
                worker,
                "find_post_apply_cover_letter_trigger",
                side_effect=[None, trigger],
            ),
            patch.object(worker, "find_visible", return_value=None),
            patch.object(worker, "detect_manual_required", return_value=None),
        ):
            self.assertTrue(
                worker.wait_for_post_apply_transition(
                    self.page,
                    attempts=5,
                    delay_ms=1,
                )
            )

        self.page.wait_for_timeout.assert_called_once_with(1)

    def test_regular_cover_form_skips_post_apply_wait(self):
        field = object()
        with (
            patch.object(worker, "already_applied", return_value=False),
            patch.object(worker, "find_post_apply_cover_letter_trigger", return_value=None),
            patch.object(worker, "find_visible", return_value=field),
            patch.object(worker, "detect_manual_required", return_value=None),
        ):
            self.assertFalse(
                worker.wait_for_post_apply_transition(
                    self.page,
                    attempts=5,
                    delay_ms=1,
                )
            )

        self.page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
