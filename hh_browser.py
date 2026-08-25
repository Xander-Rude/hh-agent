from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "browser-profile"
RESUMES_URL = "https://hh.ru/applicant/resumes"


def hh_cookie_names(page: Page) -> set[str]:
    try:
        cookies = page.context.cookies("https://hh.ru")
    except Exception:
        return set()

    return {
        str(cookie.get("name") or "").lower()
        for cookie in cookies
    }


def hh_is_authenticated(page: Page) -> bool:
    """Conservative check that the shared Playwright HH session is alive."""
    url = (page.url or "").lower()

    if (
        "account/login" in url
        or "/login" in url
        or "account/signup" in url
    ):
        return False

    # hhtoken is the strongest cheap signal we have in the persistent profile.
    if "hhtoken" in hh_cookie_names(page):
        return True

    try:
        if page.locator('[data-qa="resume"]').count() > 0:
            return True
    except Exception:
        pass

    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        text = ""

    logged_in_markers = (
        "мои резюме",
        "резюме и профиль",
        "статус поиска",
        "создать резюме",
    )
    if any(marker in text for marker in logged_in_markers):
        return True

    logged_out_markers = (
        "войти",
        "зарегистрироваться",
        "вход для соискателя",
    )
    if any(marker in text for marker in logged_out_markers):
        return False

    # Unknown state: do not silently claim that the session is valid.
    return False
