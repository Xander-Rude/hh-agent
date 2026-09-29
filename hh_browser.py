from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from hh_accounts import HHAccount, active_apply_account, get_account


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = active_apply_account().profile_dir
RESUMES_URL = "https://hh.ru/applicant/resumes"
DEFAULT_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

OLD_CDP_URL = (
    os.getenv("HH_OLD_CDP_URL")
    or "http://127.0.0.1:9224"
).strip().rstrip("/")
OLD_CDP_PORT = int(os.getenv("HH_OLD_CDP_PORT", "9224"))
OLD_CDP_ENABLED = (
    os.getenv("HH_OLD_USE_CDP", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
OLD_CHROME_EXE = Path(
    os.getenv(
        "HH_OLD_CHROME_EXE",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
)
OLD_CDP_START_TIMEOUT_SECONDS = max(
    2.0,
    float(os.getenv("HH_OLD_CDP_START_TIMEOUT_SECONDS", "12")),
)


def hh_browser_context_options(*, headless: bool) -> dict:
    """Shared Chromium options for non-CDP HH browser contexts.

    CLEAN and test contexts may still use Playwright-managed Chromium.
    OLD production uses a long-lived normal Chrome process over CDP instead,
    because HH authentication is not reliable in Playwright-launched OLD
    contexts and repeated launches caused visible browser flashing.
    """
    options = {
        "headless": headless,
        "viewport": {"width": 1440, "height": 1000},
    }
    if headless:
        options["user_agent"] = (
            os.getenv("HH_HEADLESS_USER_AGENT")
            or DEFAULT_HEADLESS_USER_AGENT
        ).strip()
    return options


def profile_dir_for(account_key: str) -> Path:
    return get_account(account_key).profile_dir


def _account_item(account: HHAccount | str) -> HHAccount:
    return get_account(account) if isinstance(account, str) else account


def _old_cdp_ready() -> bool:
    try:
        with urllib.request.urlopen(
            OLD_CDP_URL + "/json/version",
            timeout=1.0,
        ) as response:
            return int(getattr(response, "status", 200)) == 200
    except Exception:
        return False


def ensure_old_cdp_browser() -> None:
    """Keep one normal Chrome instance alive for the OLD HH profile."""
    if not OLD_CDP_ENABLED:
        raise RuntimeError("HH_OLD_USE_CDP=false")
    if _old_cdp_ready():
        return
    if os.name != "nt":
        raise RuntimeError("OLD HH CDP browser currently requires Windows")
    if not OLD_CHROME_EXE.exists():
        raise RuntimeError(f"Chrome not found: {OLD_CHROME_EXE}")

    account = get_account("old")
    flags = 0
    flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))

    subprocess.Popen(
        [
            str(OLD_CHROME_EXE),
            f"--user-data-dir={account.profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={OLD_CDP_PORT}",
            "--no-first-run",
            "--start-minimized",
            RESUMES_URL,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )

    deadline = time.monotonic() + OLD_CDP_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _old_cdp_ready():
            return
        time.sleep(0.25)

    raise RuntimeError(
        "OLD Chrome CDP did not start. Close any manually opened OLD HH "
        "browser-profile window and retry."
    )


@dataclass
class HHBrowserSession:
    context: Any
    page: Any
    external_browser: Any | None = None

    def close(self) -> None:
        if self.external_browser is not None:
            try:
                if self.page is not None and not self.page.is_closed():
                    self.page.close()
            except Exception:
                pass
            return

        try:
            self.context.close()
        except Exception:
            pass


def open_hh_browser(
    playwright,
    *,
    account: HHAccount | str,
    headless: bool,
) -> HHBrowserSession:
    """Open an isolated HH browser session.

    OLD attaches to one persistent normal Chrome over CDP. Other accounts
    retain the existing Playwright persistent-context behaviour.
    """
    item = _account_item(account)

    if item.key == "old" and OLD_CDP_ENABLED:
        ensure_old_cdp_browser()
        browser = playwright.chromium.connect_over_cdp(OLD_CDP_URL)
        if not browser.contexts:
            raise RuntimeError("OLD Chrome CDP has no browser context")
        context = browser.contexts[0]
        page = context.new_page()
        return HHBrowserSession(
            context=context,
            page=page,
            external_browser=browser,
        )

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(item.profile_dir),
        **hh_browser_context_options(headless=headless),
    )
    page = context.pages[0] if context.pages else context.new_page()
    return HHBrowserSession(
        context=context,
        page=page,
    )


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
    """Conservative check that the shared Playwright HH session is alive.

    Important: a stale hhtoken cookie is NOT enough. HH can keep that
    cookie while the server-side applicant session is no longer usable. We
    therefore require evidence from the rendered authenticated UI.
    """
    url = (page.url or "").lower()

    if (
        "account/login" in url
        or "/login" in url
        or "account/signup" in url
    ):
        return False

    try:
        if page.locator('[data-qa="resume"]').count() > 0:
            return True
    except Exception:
        pass

    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        text = ""

    logged_out_markers = (
        "войти",
        "зарегистрироваться",
        "вход для соискателя",
    )
    if any(marker in text for marker in logged_out_markers):
        return False

    logged_in_markers = (
        "мои резюме",
        "резюме и профиль",
        "статус поиска",
        "создать резюме",
        "поднять в поиске",
    )
    if any(marker in text for marker in logged_in_markers):
        return True

    return False
