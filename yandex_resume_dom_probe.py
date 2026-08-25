from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

from yandex_apply_dry_run import click_apply, pick_vacancy
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated
from yandex_profile_edit_dry_run import click_edit


HEADLESS = False


def find_profile_form(page):
    forms = page.locator("form")
    for index in range(forms.count()):
        form = forms.nth(index)
        try:
            text = form.inner_text(timeout=1000)
        except Exception:
            continue
        if "Резюме" in text and "Сохранить данные" in text:
            return form
    return None


def find_resume_section(page):
    form = find_profile_form(page)
    if form is None:
        return None

    heading = form.get_by_text("Резюме", exact=True)
    if not heading.count():
        return form

    node = heading.first
    best = form
    for level in range(1, 8):
        try:
            candidate = node.locator("xpath=" + "/.." * level)
            text = " ".join((candidate.inner_text(timeout=500) or "").split())
        except Exception:
            continue

        if "Файл" in text and "Ссылка" in text:
            best = candidate
            if "Сохранить данные" not in text:
                return candidate

    return best


def print_resume_dom(page) -> None:
    section = find_resume_section(page)
    if section is None:
        print("[ERROR] Блок «Резюме» не найден")
        return

    print("\n" + "=" * 80)
    print("RESUME DOM PROBE — НИЧЕГО НЕ КЛИКАЕТСЯ И НЕ СОХРАНЯЕТСЯ")
    print("=" * 80)

    try:
        text = " ".join((section.inner_text(timeout=2000) or "").split())
    except Exception:
        text = ""
    print(f"[TEXT] {text}")

    try:
        html = section.evaluate("el => el.outerHTML")
        html = " ".join(str(html).split())
    except Exception as exc:
        html = f"<error {type(exc).__name__}: {exc}>"

    print("\n[HTML]")
    print(html[:12000])

    nodes = section.locator("input, button, label, a, div, span, svg")
    print(f"\n[NODES] count={nodes.count()}")

    for index in range(min(nodes.count(), 150)):
        node = nodes.nth(index)
        try:
            meta = node.evaluate(
                """el => {
                    const attrs = {};
                    for (const a of el.attributes || []) attrs[a.name] = a.value;
                    return {
                        tag: el.tagName,
                        text: (el.innerText || el.textContent || '').trim(),
                        attrs,
                        cursor: getComputedStyle(el).cursor,
                        display: getComputedStyle(el).display,
                        visibility: getComputedStyle(el).visibility,
                    };
                }"""
            )
        except Exception as exc:
            print(f"[{index}] inspect failed: {type(exc).__name__}: {exc}")
            continue

        text = " ".join(str(meta.get("text") or "").split())
        attrs = meta.get("attrs") or {}

        interesting = (
            meta.get("tag") in {"INPUT", "BUTTON", "LABEL", "A"}
            or text
            or attrs.get("aria-label")
            or attrs.get("title")
            or attrs.get("role")
            or attrs.get("tabindex")
            or attrs.get("class")
        )
        if not interesting:
            continue

        print(
            f"[{index}] tag={meta.get('tag')} "
            f"text={text[:140]!r} "
            f"cursor={meta.get('cursor')} "
            f"attrs={json.dumps(attrs, ensure_ascii=False)[:1200]}"
        )


def main() -> int:
    vacancy, evaluation = pick_vacancy()

    print("=" * 80)
    print("YANDEX RESUME DOM PROBE")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Decision: {evaluation.decision}")
    print(f"URL: {vacancy.url}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = get_page(context)
            page.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована")
                return 4

            if not click_apply(page):
                print("[ERROR] Не нашёл кнопку «Откликнуться»")
                return 5

            print("[OK] Блок отклика открыт")

            if not click_edit(page):
                print("[ERROR] Не нашёл кнопку «Редактировать»")
                return 6

            print("[OK] Редактор профиля открыт")
            print_resume_dom(page)

            print("\n[SAFE] Ничего не кликалось, не удалялось, не загружалось и не сохранялось.")
            input("\nНажмите Enter для выхода...")
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
