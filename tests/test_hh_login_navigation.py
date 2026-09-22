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


    def test_already_authenticated_profile_is_persisted_without_waiting(self) -> None:
        account = MagicMock()
        account.label = "🟢 CLEAN"
        page = MagicMock()

        with patch.object(hh_login, "hh_is_authenticated", return_value=True):
            with patch.object(
                hh_login,
                "_save_authenticated_account",
                return_value=["clean-resume-id"],
            ) as save:
                self.assertTrue(
                    hh_login._persist_if_already_authenticated(account, page)
                )

        save.assert_called_once_with(account, page)

    def test_confirm_authenticated_returns_false_when_page_is_closed(self) -> None:
        page = MagicMock()
        page.wait_for_timeout.side_effect = PlaywrightError(
            "Page.wait_for_timeout: Target page, context or browser has been closed"
        )

        self.assertFalse(hh_login._confirm_authenticated(page))


    def test_safe_goto_reraises_unrelated_playwright_error(self) -> None:
        page = MagicMock()
        page.goto.side_effect = PlaywrightError("Page.goto: net::ERR_FAILED")

        with self.assertRaises(PlaywrightError):
            hh_login._safe_goto_resumes(page)


if __name__ == "__main__":
    unittest.main()
