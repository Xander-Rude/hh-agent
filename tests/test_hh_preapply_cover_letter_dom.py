"""DOM regressions for HH pre-apply cover-letter routing."""
import os
import unittest

from playwright.sync_api import sync_playwright

import apply_worker as worker


@unittest.skipUnless(os.getenv("HH_APPLY_DOM_TESTS") == "1", "Browser checks run in CI")
class HHPreApplyCoverLetterDOMTests(unittest.TestCase):
    def test_direct_cover_letter_link_opens_form_before_response(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <a id="letter-link" href="#">Написать сопроводительное</a>
                <div id="dialog" hidden>
                  <textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>
                  <button data-qa="vacancy-response-submit-popup">Откликнуться</button>
                </div>
                <script>
                  document.getElementById('letter-link').addEventListener('click', event => {
                    event.preventDefault();
                    document.getElementById('dialog').hidden = false;
                  });
                </script>
                """
            )

            self.assertTrue(worker.try_open_preapply_cover_letter(page))
            self.assertTrue(
                page.locator(
                    'textarea[data-qa="vacancy-response-popup-form-letter-input"]'
                ).is_visible()
            )
            browser.close()

    def test_dropdown_with_letter_option_opens_form_before_response(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <a data-qa="vacancy-response-link-top">Откликнуться</a>
                <button id="arrow" type="button">▼</button>
                <div id="menu" hidden>
                  <button id="with-letter" type="button">С сопроводительным письмом</button>
                </div>
                <div id="dialog" hidden>
                  <textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>
                  <button data-qa="vacancy-response-submit-popup">Откликнуться</button>
                </div>
                <script>
                  document.getElementById('arrow').addEventListener('click', () => {
                    document.getElementById('menu').hidden = false;
                  });
                  document.getElementById('with-letter').addEventListener('click', () => {
                    document.getElementById('dialog').hidden = false;
                  });
                </script>
                """
            )

            self.assertTrue(worker.try_open_preapply_cover_letter(page))
            self.assertTrue(
                page.locator(
                    'textarea[data-qa="vacancy-response-popup-form-letter-input"]'
                ).is_visible()
            )
            browser.close()


if __name__ == "__main__":
    unittest.main()
