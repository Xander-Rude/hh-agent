"""DOM regressions for HH employer screening vs cover-letter fields."""
import os
import unittest

from playwright.sync_api import sync_playwright

import apply_worker as worker


@unittest.skipUnless(os.getenv("HH_APPLY_DOM_TESTS") == "1", "Browser checks run in CI")
class HHScreeningGuardDOMTests(unittest.TestCase):
    def test_screening_form_is_manual_required_structurally(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form id="RESPONSE_MODAL_FORM_ID">
                  <input type="hidden" name="testRequired" value="true">
                  <textarea name="task_360978399_text"></textarea>
                  <textarea name="task_360978400_text"></textarea>
                  <button type="submit" data-qa="vacancy-response-submit-popup">
                    Откликнуться
                  </button>
                </form>
                """
            )

            self.assertEqual(
                worker.detect_manual_required(page),
                "вопросы работодателя",
            )
            browser.close()

    def test_task_textarea_is_never_cover_letter_field(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form>
                  <textarea name="task_360978399_text"></textarea>
                  <button type="button">Добавить сопроводительное письмо</button>
                </form>
                """
            )

            self.assertIsNone(worker.ensure_cover_letter_field(page))
            self.assertEqual(
                page.locator('textarea[name^="task_"]').input_value(),
                "",
            )
            browser.close()

    def test_real_cover_letter_field_is_still_used(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form>
                  <textarea name="task_360978399_text"></textarea>
                  <textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>
                </form>
                """
            )

            self.assertTrue(worker.fill_cover_letter(page, "Точное письмо"))
            self.assertEqual(
                page.locator('textarea[data-qa="vacancy-response-popup-form-letter-input"]').input_value(),
                "Точное письмо",
            )
            self.assertEqual(
                page.locator('textarea[name^="task_"]').input_value(),
                "",
            )
            browser.close()


if __name__ == "__main__":
    unittest.main()
