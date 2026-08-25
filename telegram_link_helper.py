from __future__ import annotations

from app.db import Vacancy


def vacancy_button(vacancy: Vacancy) -> tuple[str, str]:
    """Return source-aware Telegram button label and canonical vacancy URL."""
    source = (vacancy.source or "hh").strip().lower()
    url = (vacancy.url or "").strip()

    if source == "yandex":
        return "🔗 Открыть Yandex", url

    return "🔗 Открыть HH", url
