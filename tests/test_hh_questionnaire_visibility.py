"""Safety regressions for the read-only HH resume-visibility check."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.hh_questionnaire_visibility import confirmed_public_visibility

RID = "b5d6fbf3ff1124b2890039ed1f394633454535"


def page_with_card(text: str, *, url: str | None = None, visible=True, count=1):
    card = Mock()
    card.is_visible.return_value = visible
    card.inner_text.return_value = text
    cards = Mock()
    cards.count.return_value = count
    cards.first = card

    tab = Mock()
    tab.url = url or "https://hh.ru/resume/" + RID + "?hhtmFrom=profile"
    tab.locator.return_value = cards
    parent = Mock()
    parent.context.new_page.return_value = tab
    return parent, tab


class HHQuestionnaireVisibilityTests(unittest.TestCase):
    def test_real_hh_public_label_overrides_stale_questionnaire_warning(self):
        parent, tab = page_with_card(
            "Видимость резюме\nВидно всем работодателям, зарегистрированным на\xa0hh.ru"
        )
        self.assertTrue(confirmed_public_visibility(parent, RID))
        tab.goto.assert_called_once()
        self.assertEqual(tab.goto.call_args.args[0], "https://hh.ru/resume/" + RID + "?hhtmFrom=profile")
        tab.locator.assert_called_once_with('[data-qa="resume-visibility-card"]')
        tab.close.assert_called_once()

    def test_private_resume_must_still_block(self):
        for label in (
            "Видимость резюме\nНе видно никому",
            "Видимость резюме\nВидно только по ссылке",
            "Видимость резюме\nВидно выбранным работодателям",
            "Видно всем работодателям, зарегистрированным на hh.ru",
            "Видимость резюме\nВидно всем работодателям, кроме MANGO OFFICE",
        ):
            with self.subTest(label=label):
                parent, tab = page_with_card(label)
                self.assertFalse(confirmed_public_visibility(parent, RID))
                tab.close.assert_called_once()

    def test_redirect_wrong_id_hidden_card_or_missing_card_block(self):
        for url in (
            "https://hh.ru/account/login",
            "https://evil.example/resume/" + RID,
            "https://hh.ru/resume/" + "f" * 32,
        ):
            with self.subTest(url=url):
                parent, tab = page_with_card(
                    "Видимость резюме\nВидно всем работодателям, зарегистрированным на hh.ru",
                    url=url,
                )
                self.assertFalse(confirmed_public_visibility(parent, RID))
                tab.close.assert_called_once()
        for kw in ({"visible": False}, {"count": 0}, {"count": 2}):
            parent, tab = page_with_card(
                "Видимость резюме\nВидно всем работодателям, зарегистрированным на hh.ru",
                **kw,
            )
            self.assertFalse(confirmed_public_visibility(parent, RID))
            tab.close.assert_called_once()

    def test_invalid_resume_id_does_not_open_browser(self):
        parent = Mock()
        for value in (None, "", "not-a-resume", RID + "garbage"):
            self.assertFalse(confirmed_public_visibility(parent, value))
        parent.context.new_page.assert_not_called()

    def test_navigation_failure_blocks_and_closes_tab(self):
        parent, tab = page_with_card("")
        tab.goto.side_effect = RuntimeError("navigation failed")
        self.assertFalse(confirmed_public_visibility(parent, RID))
        tab.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
