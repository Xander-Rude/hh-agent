from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error as PlaywrightError

import hh_login


class HHLoginNavigationTests(unittest.TestCase):
    def test_confirm_auth_survives_hh_redirect_race(self) -> None:
        page = MagicMock()
        page.goto.side_effect = PlaywrightError(
            'Page.goto: Navigation to "https://hh.ru/applicant/resumes" '
            'is interrupted by another navigation to '
            '"https://hh.ru/applicant/profile/me"'
        )

        with patch.object(
            hh_login,
            "hh_is_authenticated",
            side_effect=[False, True],
        ):
            self.assertTrue(hh_login._confirm_authenticated(page))

        page.wait_for_load_state.assert_called_once()

    def test_safe_goto_reraises_unrelated_playwright_error(self) -> None:
        page = MagicMock()
        page.goto.side_effect = PlaywrightError("Page.goto: net::ERR_FAILED")

        with self.assertRaises(PlaywrightError):
            hh_login._safe_goto_resumes(page)


if __name__ == "__main__":
    unittest.main()
