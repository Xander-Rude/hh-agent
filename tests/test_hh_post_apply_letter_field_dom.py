"""Regression checks for HH post-apply cover-letter field targeting."""
import os
import unittest

from playwright.sync_api import sync_playwright

import apply_dispatcher as dispatcher


@unittest.skipUnless(os.getenv("HH_APPLY_DOM_TESTS") == "1", "Browser checks run in CI")
class HHPostApplyLetterFieldDOMTests(unittest.TestCase):
    def test_does_not_use_unrelated_preexisting_textarea(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <textarea id="unrelated"></textarea>
                <button id="trigger" data-qa="vacancy-response-letter-trigger">
                  Приложить сопроводительное письмо
                </button>
                <div id="editor" hidden>
                  <textarea id="letter" name="letter"></textarea>
                </div>
                <script>
                  document.getElementById('trigger').addEventListener('click', () => {
                    document.getElementById('editor').hidden = false;
                  });
                </script>
                """
            )

            field = dispatcher._hh_find_post_apply_cover_letter_field(page)

            self.assertIsNotNone(field)
            self.assertEqual(field.get_attribute("id"), "letter")
            self.assertEqual(
                page.locator("#unrelated").get_attribute(
                    dispatcher._HH_PREEXISTING_TEXTAREA_ATTR
                ),
                "1",
            )
            browser.close()

    def test_accepts_single_new_generic_textarea_after_explicit_trigger(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <textarea id="unrelated"></textarea>
                <button id="trigger" data-qa="vacancy-response-letter-trigger">
                  Добавить сопроводительное письмо
                </button>
                <div id="editor" hidden>
                  <textarea id="letter-generic"></textarea>
                </div>
                <script>
                  document.getElementById('trigger').addEventListener('click', () => {
                    document.getElementById('editor').hidden = false;
                  });
                </script>
                """
            )

            field = dispatcher._hh_find_post_apply_cover_letter_field(page)

            self.assertIsNotNone(field)
            self.assertEqual(field.get_attribute("id"), "letter-generic")
            self.assertEqual(
                field.get_attribute(dispatcher._HH_POST_APPLY_FIELD_ATTR),
                "1",
            )
            browser.close()

    def test_returns_none_when_only_unrelated_textarea_exists(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content('<textarea id="unrelated"></textarea>')

            field = dispatcher._hh_find_post_apply_cover_letter_field(page)

            self.assertIsNone(field)
            self.assertEqual(page.locator("#unrelated").input_value(), "")
            browser.close()

    def test_submit_prefers_letter_specific_control_over_generic_submit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form id="letter-form">
                  <textarea name="letter"></textarea>
                  <button type="submit" data-qa="other-submit">Продолжить</button>
                  <button type="button" data-qa="cover-letter-submit">Сохранить письмо</button>
                </form>
                """
            )

            submit = dispatcher._hh_find_letter_submit_robust(
                page.locator('textarea[name="letter"]')
            )

            self.assertIsNotNone(submit)
            self.assertEqual(submit.get_attribute("data-qa"), "cover-letter-submit")
            browser.close()


if __name__ == "__main__":
    unittest.main()
