from playwright.sync_api import sync_playwright

from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1440, "height": 1000},
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto(
        RESUMES_URL,
        wait_until="domcontentloaded",
    )

    print(f"PROFILE: {PROFILE_DIR}")
    print(f"URL: {page.url}")

    if hh_is_authenticated(page):
        print("[OK] Профиль уже авторизован на hh.ru.")
    else:
        print("[ACTION] Войди в HH в открытом окне браузера.")

    input("После успешного входа нажми Enter здесь, чтобы закрыть браузер...")

    page.goto(
        RESUMES_URL,
        wait_until="domcontentloaded",
    )

    if hh_is_authenticated(page):
        print("[OK] Авторизация сохранена в общем browser-profile.")
    else:
        print("[ERROR] HH-сессия не подтверждена. Профиль не считаю авторизованным.")

    ctx.close()
