from pathlib import Path
from playwright.sync_api import sync_playwright

profile = Path(r"C:\hh-agent\browser-profile")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto(
        "https://hh.ru/applicant/resumes",
        wait_until="domcontentloaded"
    )

    print()
    print("FINAL URL:", page.url)
    print("TITLE:", page.title())

    cookies = ctx.cookies("https://hh.ru")
    print("HH cookies:", len(cookies))
    print("Cookie names:", [c["name"] for c in cookies])

    input("Press Enter to close...")

    ctx.close()
