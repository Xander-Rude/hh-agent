"""Read-only confirmation of HH resume visibility for employer questionnaires.

HH sometimes renders the hidden-resume-warning even while the selected resume
is already globally visible. The warning itself is never enough to infer
visibility; only the selected resume's actual settings can override it.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


_PUBLIC_VISIBILITY = re.compile(
    r"видимость резюме видно всем работодателям, зарегистрированным на hh\.ru",
    re.I,
)
_RESUME_ID = re.compile(r"[0-9a-f]{32}", re.I)


def confirmed_public_visibility(page, resume_id: str) -> bool:
    """Return True only for the exact selected HH resume and exact public setting.

    Must be invoked on the already-authenticated, account-bound Playwright
    browser context. Creates a read-only tab, never changes visibility.
    Fail closed for a login redirect, missing/multiple cards or any UI error.
    """
    if not isinstance(resume_id, str) or not _RESUME_ID.fullmatch(resume_id):
        return False

    tab = None
    try:
        tab = page.context.new_page()
        expected_path = "/resume/" + resume_id
        tab.goto(
            "https://hh.ru" + expected_path + "?hhtmFrom=profile",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        if (
            urlparse(tab.url).hostname not in {"hh.ru", "www.hh.ru"}
            or urlparse(tab.url).path.lower() != expected_path.lower()
        ):
            return False

        cards = tab.locator('[data-qa="resume-visibility-card"]')
        if cards.count() != 1 or not cards.first.is_visible():
            return False
        text = " ".join(cards.first.inner_text(timeout=4000).split())
        return bool(_PUBLIC_VISIBILITY.fullmatch(text))
    except Exception as exc:
        print(
            "[HH QUESTIONNAIRE] Public visibility check unavailable: "
            f"{type(exc).__name__}",
            flush=True,
        )
        return False
    finally:
        if tab is not None:
            try:
                tab.close()
            except Exception:
                pass
