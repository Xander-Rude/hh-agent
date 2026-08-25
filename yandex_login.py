from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from yandex_browser import (
    PROFILE_DIR,
    YANDEX_JOBS_URL,
    auth_cookie_names,
    get_page,
    is_yandex_authenticated,
)


def open_jobs_allowing_auth_redirect(page) -> None:
    """Открывает Yandex Jobs, считая редирект на id.yandex.ru штатным.

    Для неавторизованной сессии Яндекс может сам начать вторую навигацию на
    страницу авторизации раньше, чем завершится исходный page.goto(). Playwright
    в таком случае выбрасывает ошибку "interrupted by another navigation",
    хотя браузер уже находится именно там, где нам нужно для ручного входа.
    """
    try:
        page.goto(
            YANDEX_JOBS_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
    except PlaywrightError as exc:
        message = str(exc)
        current_url = page.url or ""

        interrupted = "interrupted by another navigation" in message
        redirected_to_yandex_auth = (
            "id.yandex." in current_url
            or "passport.yandex." in current_url
        )

        if interrupted and redirected_to_yandex_auth:
            print(f"[INFO] Яндекс перенаправил на авторизацию: {current_url}")
            return

        raise


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
            open_jobs_allowing_auth_redirect(page)

            print()
            print("В открывшемся браузере войдите в аккаунт Яндекса.")
            print("После успешного входа вернитесь в консоль и нажмите Enter.")
            input()

            # После ручной авторизации заново открываем Jobs, чтобы cookies и
            # состояние аккаунта точно применились к рабочему домену.
            open_jobs_allowing_auth_redirect(page)
            page.wait_for_timeout(1500)

            authenticated = is_yandex_authenticated(page, context)

            print(f"FINAL URL: {page.url}")
            print(f"TITLE: {page.title()}")
            print(f"AUTHENTICATED: {authenticated}")
            print(f"YANDEX cookie names: {auth_cookie_names(context)}")

            if not authenticated:
                print("[ERROR] Не удалось подтвердить авторизацию Яндекса.")
                return 4

            print("[OK] Сессия Яндекса сохранена.")
            return 0

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
