from playwright.sync_api import sync_playwright

from hh_browser import PROFILE_DIR, RESUMES_URL, hh_cookie_names, hh_is_authenticated


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto(
        RESUMES_URL,
        wait_until="domcontentloaded",
    )

    print()
    print("PROFILE:", PROFILE_DIR)
    print("FINAL URL:", page.url)
    print("TITLE:", page.title())
    print("AUTHENTICATED:", hh_is_authenticated(page))
    print("HH cookie names:", sorted(hh_cookie_names(page)))

    input("Press Enter to close...")

    ctx.close()
