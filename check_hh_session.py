from __future__ import annotations

import argparse

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from hh_accounts import all_accounts, get_account
from hh_browser import RESUMES_URL, hh_cookie_names, hh_is_authenticated, open_hh_browser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check isolated HH sessions.")
    parser.add_argument(
        "--account",
        choices=("old", "clean", "all"),
        default="all",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = (
        all_accounts()
        if args.account == "all"
        else (get_account(args.account),)
    )

    with sync_playwright() as p:
        for account in accounts:
            browser_session = open_hh_browser(
                p,
                account=account,
                headless=False,
            )
            try:
                page = browser_session.page
                page.goto(RESUMES_URL, wait_until="domcontentloaded")

                print()
                print("ACCOUNT:", account.label)
                print("PROFILE:", account.profile_dir)
                print("FINAL URL:", page.url)
                print("TITLE:", page.title())
                print("AUTHENTICATED:", hh_is_authenticated(page))
                print("HH cookie names:", sorted(hh_cookie_names(page)))
                print()
                input("Press Enter to close this account check...")
            finally:
                browser_session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
