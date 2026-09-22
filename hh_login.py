from __future__ import annotations

import argparse
import re

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from hh_accounts import get_account, write_account_state
from hh_browser import RESUMES_URL, hh_is_authenticated


RESUME_ID_RE = re.compile(r"/resume/([0-9a-f]+)", re.IGNORECASE)


def _safe_goto_resumes(page) -> None:
    try:
        page.goto(RESUMES_URL, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        message = str(exc).lower()
        if "interrupted by another navigation" not in message:
            raise

        # HH may redirect immediately after login (for example to
        # /applicant/profile/me). That redirect is legitimate and means the
        # persistent session is still progressing, not that login failed.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightError:
            pass


def _confirm_authenticated(page) -> bool:
    page.wait_for_timeout(750)

    if hh_is_authenticated(page):
        return True

    # If the current post-login page is ambiguous, make one best-effort visit
    # to the resumes page. A concurrent HH redirect is tolerated by
    # _safe_goto_resumes.
    _safe_goto_resumes(page)
    page.wait_for_timeout(750)
    return hh_is_authenticated(page)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authenticate an isolated HH account profile."
    )
    parser.add_argument(
        "--account",
        choices=("old", "clean"),
        default="clean",
        help="HH account profile to authenticate. Default: clean.",
    )
    return parser.parse_args()


def discover_resume_ids(page) -> list[str]:
    result: list[str] = []
    try:
        links = page.locator('a[href*="/resume/"]')
        count = links.count()
    except Exception:
        return result

    for index in range(count):
        try:
            href = links.nth(index).get_attribute("href") or ""
        except Exception:
            continue
        match = RESUME_ID_RE.search(href)
        if match:
            result.append(match.group(1))

    return list(dict.fromkeys(result))


def main() -> int:
    args = parse_args()
    account = get_account(args.account)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(account.profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _safe_goto_resumes(page)

            print(f"ACCOUNT: {account.label}")
            print(f"PROFILE: {account.profile_dir}")
            print(f"URL: {page.url}")

            if hh_is_authenticated(page):
                print("[OK] Профиль уже авторизован на hh.ru.")
            else:
                print("[ACTION] Войди в HH в открытом окне браузера.")

            input("После успешного входа нажми Enter здесь, чтобы продолжить...")

            if not _confirm_authenticated(page):
                write_account_state(account, authenticated=False)
                print("[ERROR] HH-сессия не подтверждена.")
                return 4

            # Authentication is already confirmed. Opening /resumes is only
            # for optional resume-id discovery, so an HH redirect race must
            # never invalidate a successful login.
            try:
                _safe_goto_resumes(page)
                page.wait_for_timeout(750)
            except PlaywrightError:
                pass

            resume_ids = discover_resume_ids(page)
            write_account_state(
                account,
                authenticated=True,
                resume_ids=resume_ids,
            )

            print(f"[OK] Авторизация сохранена: {account.label}.")
            if resume_ids:
                print("[OK] Resume IDs: " + ", ".join(resume_ids))
            else:
                print("[WARN] Resume ID автоматически определить не удалось.")
            return 0
        finally:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
