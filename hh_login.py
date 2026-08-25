from pathlib import Path
from playwright.sync_api import sync_playwright

profile = Path(r"C:\hh-agent\browser-profile")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        viewport={"width": 1440, "height": 1000},
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto(
        "https://hh.ru/applicant/resumes",
        wait_until="domcontentloaded"
    )

    input("Login to HH. Then press Enter here to close browser...")

    ctx.close()
