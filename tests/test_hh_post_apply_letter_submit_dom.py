"""Regression checks for HH post-apply cover-letter submit discovery."""
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO

from playwright.sync_api import sync_playwright

import apply_dispatcher as dispatcher


@unittest.skipUnless(os.getenv("HH_APPLY_DOM_TESTS") == "1", "Browser checks run in CI")
class HHPostApplyLetterSubmitDOMTests(unittest.TestCase):
    def test_finds_form_submit_without_known_caption(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form id="letter-form">
                  <div><textarea name="letter"></textarea></div>
                  <div><button type="submit" data-qa="vacancy-response-letter-save">Готово</button></div>
                </form>
                """
            )
            field = page.locator("textarea")
            submit = dispatcher._hh_find_letter_submit_robust(field)
            self.assertIsNotNone(submit)
            self.assertEqual(submit.get_attribute("data-qa"), "vacancy-response-letter-save")
            browser.close()

    def test_finds_letter_specific_button_without_form(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <div role="dialog">
                  <div><textarea name="letter"></textarea></div>
                  <div><button data-qa="cover-letter-submit">Подтвердить</button></div>
                </div>
                """
            )
            submit = dispatcher._hh_find_letter_submit_robust(page.locator("textarea"))
            self.assertIsNotNone(submit)
            self.assertEqual(submit.get_attribute("data-qa"), "cover-letter-submit")
            browser.close()

    def test_never_uses_vacancy_apply_button(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <div role="dialog">
                  <textarea name="letter"></textarea>
                  <button data-qa="vacancy-response-link-top">Откликнуться</button>
                </div>
                """
            )
            output = StringIO()
            with redirect_stdout(output):
                submit = dispatcher._hh_find_letter_submit_robust(page.locator("textarea"))
            self.assertIsNone(submit)
            self.assertIn("HH post-apply letter controls", output.getvalue())
            self.assertIn("vacancy-response-link-top", output.getvalue())
            browser.close()

    def test_text_fallback_is_partial_not_exact(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <div role="dialog">
                  <textarea name="letter"></textarea>
                  <button>Сохранить сопроводительное письмо</button>
                </div>
                """
            )
            submit = dispatcher._hh_find_letter_submit_robust(page.locator("textarea"))
            self.assertIsNotNone(submit)
            self.assertEqual(submit.inner_text(), "Сохранить сопроводительное письмо")
            browser.close()


if __name__ == "__main__":
    unittest.main()
