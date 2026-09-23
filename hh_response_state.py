from __future__ import annotations


EXISTING_RESPONSE_MARKERS = (
    "вы отправили резюме",
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

REJECTED_RESPONSE_MARKERS = (
    "работодатель отказал",
    "работодатель отклонил",
    "отклик отклонен",
    "вам отказали",
    "вам отказано",
    "в отклике отказано",
    "не готов пригласить",
)

WORKFLOW_INVITATION_MARKERS = (
    "работодатель пригласил",
    "вас пригласили",
    "приглашение",
    "приглашены",
)

VIEWED_RESPONSE_MARKERS = (
    "работодатель просмотрел",
    "резюме просмотрено",
    "отклик просмотрен",
    "просмотрено работодателем",
)


def classify_hh_negotiation_text(text: str | None) -> str | None:
    """Classify HH platform state without claiming human contact.

    HH can label an automated employer workflow step as an invitation. Such a
    label is deliberately returned as workflow_invited rather than
    human_response or interview_agreed.
    """
    normalized = normalize_response_text(text)

    if any(
        normalize_response_text(marker) in normalized
        for marker in REJECTED_RESPONSE_MARKERS
    ):
        return "rejected"

    if any(
        normalize_response_text(marker) in normalized
        for marker in WORKFLOW_INVITATION_MARKERS
    ):
        return "workflow_invited"

    if any(
        normalize_response_text(marker) in normalized
        for marker in VIEWED_RESPONSE_MARKERS
    ):
        return "viewed"

    if existing_response_marker_from_text(normalized):
        return "submitted"

    return None


VACANCY_RESPONSE_STATE_SELECTORS = (
    '[data-qa^="responded-"]',
    '[data-qa*="vacancy-response"]',
)

CAREER_STATE_PRIORITY = {
    "submitted": 10,
    "viewed": 20,
    "workflow_invited": 30,
    "rejected": 100,
}


def _response_widget_texts(page) -> list[str]:
    """Collect bounded text only from response-specific vacancy UI."""
    texts: list[str] = []

    for selector in VACANCY_RESPONSE_STATE_SELECTORS:
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
                candidates = item.evaluate(
                    """
                    el => {
                      const out = [];
                      let node = el;
                      for (let depth = 0; node && depth < 4; depth += 1) {
                        const clone = node.cloneNode(true);
                        clone.querySelectorAll(
                          '[data-qa="vacancy-description"], .vacancy-description, script, style'
                        ).forEach(child => child.remove());
                        const text = (clone.innerText || '').trim();
                        if (
                          text
                          && text.length <= 2500
                          && !out.includes(text)
                        ) {
                          out.push(text);
                        }
                        node = node.parentElement;
                      }
                      return out;
                    }
                    """
                )
            except Exception:
                candidates = []

            for text in candidates or []:
                clean = " ".join(str(text).split())
                if clean and clean not in texts:
                    texts.append(clean)

    return texts


def detect_hh_vacancy_career_state(
    page,
) -> tuple[str | None, str]:
    """Return only a career state grounded in vacancy response UI.

    This deliberately does not infer state from a negotiations URL/filter.
    HH may ignore those filters, which previously fabricated invitation/
    rejection states. Generic page text is used only to confirm that an
    application exists, never to promote it to viewed/invited/rejected.
    """
    best_state: str | None = None
    best_text = ""

    for text in _response_widget_texts(page):
        state = classify_hh_negotiation_text(text)
        if state is None:
            continue
        if (
            best_state is None
            or CAREER_STATE_PRIORITY[state]
            > CAREER_STATE_PRIORITY[best_state]
        ):
            best_state = state
            best_text = text

    if best_state is not None:
        return best_state, best_text[:1200]

    existing_marker = detect_existing_hh_response(page)
    if existing_marker is not None:
        return "submitted", str(existing_marker)[:1200]

    return None, ""
