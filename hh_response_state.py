from __future__ import annotations


EXISTING_RESPONSE_MARKERS = (
    "вы уже откликнулись",
    "вы откликнулись",
    "отклик уже отправлен",
    "отклик был отправлен",
    "отклик отправлен",
    "резюме уже отправлено",
    "резюме отправлено",
    "работодатель отказал",
    "работодатель отклонил ваш отклик",
    "ваш отклик отклонен",
    "вам отказали",
    "вам отказано",
    "в отклике отказано",
    "работодатель пригласил",
    "вас пригласили",
)


EXISTING_RESPONSE_SELECTORS = (
    '[data-qa^="responded-"]',
    '[data-qa*="vacancy-response-status"]',
    '[data-qa*="response-status"]',
)


def normalize_response_text(text: str | None) -> str:
    value = (text or "").lower().replace("ё", "е")
    return " ".join(value.split())


def existing_response_marker_from_text(
    text: str | None,
) -> str | None:
    normalized = normalize_response_text(text)

    for marker in EXISTING_RESPONSE_MARKERS:
        if normalize_response_text(marker) in normalized:
            return marker

    return None


def detect_existing_hh_response(page) -> str | None:
    """
    Return a conservative marker when the authenticated HH vacancy page shows
    that this account has already responded to the vacancy.

    We first trust response-specific data-qa controls. For text fallback we
    deliberately remove the vacancy description before reading the page so an
    employer mentioning words such as "отклик" or "отказ" in the description
    cannot suppress a fresh vacancy.
    """
    for selector in EXISTING_RESPONSE_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
            except Exception:
                continue

            try:
                text = item.inner_text(timeout=1000)
            except Exception:
                text = ""

            marker = existing_response_marker_from_text(text)
            return marker or f"selector:{selector}"

    try:
        page_text_without_description = page.locator("body").evaluate(
            """
            body => {
              const clone = body.cloneNode(true);
              clone.querySelectorAll(
                '[data-qa="vacancy-description"], .vacancy-description, script, style'
              ).forEach(node => node.remove());
              return clone.innerText || '';
            }
            """
        )
    except Exception:
        return None

    return existing_response_marker_from_text(
        page_text_without_description
    )
