from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from app.resume_metrics import record_snapshot
from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated


load_dotenv()

RESUME_ID = os.getenv(
    "HH_ACTIVE_RESUME_ID",
    "ed318343ff109278200039ed1f674d474e5336",
)
HEADLESS = os.getenv("HH_RESUME_RAISE_HEADLESS", "true").lower() == "true"


def _number(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def _metric(text: str, stems: tuple[str, ...]) -> int | None:
    normalized = " ".join(text.split())
    stem = "|".join(re.escape(item) + r"\w*" for item in stems)
    patterns = (
        rf"(?:{stem})\s*[:—\-]?\s*(\d[\d\s\u00a0]*)",
        rf"(\d[\d\s\u00a0]*)\s+(?:{stem})",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return _number(match.group(1))
    return None


def _resume_text(page) -> str | None:
    cards = page.locator('[data-qa="resume"]')
    try:
        count = cards.count()
    except Exception:
        count = 0

    for index in range(count):
        card = cards.nth(index)
        try:
            if card.locator(f'a[href*="{RESUME_ID}"]').count() > 0:
                return card.inner_text(timeout=3000)
        except Exception:
            continue

    # The experiment intentionally keeps one active resume. If HH changed the
    # card markup but only one resume is rendered, using that card is still safe.
    if count == 1:
        try:
            return cards.first.inner_text(timeout=3000)
        except Exception:
            pass
    return None


def main() -> int:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(RESUMES_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            if not hh_is_authenticated(page):
                print("[TELEMETRY] HH session is not authenticated")
                return 4

            text = _resume_text(page)
            if not text:
                print(f"[TELEMETRY] Resume card not found for {RESUME_ID}")
                return 5

            views = _metric(text, ("просмотр",))
            invitations = _metric(text, ("приглашен", "приглашени"))
            print(
                "[TELEMETRY] "
                f"resume={RESUME_ID} views={views} invitations={invitations}"
            )

            if views is None and invitations is None:
                print("[TELEMETRY] HH resume card contains no recognized metrics")
                for line in text.splitlines():
                    low = line.lower()
                    if "просмотр" in low or "приглаш" in low:
                        print(f"[TELEMETRY DEBUG] {line[:300]}")
                return 6

            record_snapshot(
                RESUME_ID,
                views=views,
                invitations=invitations,
            )
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
