from __future__ import annotations

from playwright.sync_api import sync_playwright

from yandex_browser import (
    PROFILE_DIR,
    YANDEX_JOBS_URL,
    auth_cookie_names,
    get_page,
    is_yandex_authenticated,
)


def main() -> int:
    print(f"PROFILE: {PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = get_page(context)
            page.goto(
                YANDEX_JOBS_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1200)

            authenticated = is_yandex_authenticated(page, context)

            print(f"FINAL URL: {page.url}")
            print(f"TITLE: {page.title()}")
            print(f"AUTHENTICATED: {authenticated}")
            print(f"YANDEX cookie names: {auth_cookie_names(context)}")

            input("Press Enter to close... ")
            return 0 if authenticated else 4

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
