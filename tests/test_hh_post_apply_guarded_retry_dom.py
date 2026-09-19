"""Regression check for Application 1403 guarded cover-letter retry."""
import os
import unittest

from playwright.sync_api import sync_playwright

import apply_dispatcher as dispatcher
import apply_worker as hh_worker


@unittest.skipUnless(os.getenv("HH_APPLY_DOM_TESTS") == "1", "Browser checks run in CI")
class HHPostApplyGuardedRetryDOMTests(unittest.TestCase):
    def test_retry_requires_same_visible_unchanged_form(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form>
                  <textarea data-qa="vacancy-response-letter-input">prepared letter</textarea>
                  <button type="submit" data-qa="vacancy-response-letter-submit">
                    Отправить
                  </button>
                </form>
                """
            )

            field = page.locator('textarea[data-qa="vacancy-response-letter-input"]')
            submit = page.locator('button[data-qa="vacancy-response-letter-submit"]')

            self.assertTrue(
                dispatcher._hh_post_apply_form_still_unsent(
                    field,
                    submit,
                    "prepared letter",
                )
            )

            field.fill("changed")
            self.assertFalse(
                dispatcher._hh_post_apply_form_still_unsent(
                    field,
                    submit,
                    "prepared letter",
                )
            )
            browser.close()

    def test_silent_success_detects_post_apply_letter_trigger(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <button
                  data-qa="responded-success-attach-cover-letter"
                  type="button"
                >
                  Приложить сопроводительное письмо
                </button>
                """
            )

            trigger = hh_worker.find_post_apply_cover_letter_trigger(page)

            self.assertIsNotNone(trigger)
            self.assertEqual(
                trigger.get_attribute("data-qa"),
                "responded-success-attach-cover-letter",
            )
            browser.close()

    def test_keyboard_resync_replays_controlled_textarea_events(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <textarea id="letter">stale</textarea>
                <script>
                  document.getElementById('letter').addEventListener('input', () => {
                    document.body.dataset.inputSeen = '1';
                  });
                </script>
                """
            )
            field = page.locator("#letter")

            self.assertTrue(
                dispatcher._hh_resync_letter_field_for_retry(
                    field,
                    "prepared letter",
                )
            )
            self.assertEqual(field.input_value(), "prepared letter")
            self.assertEqual(
                page.locator("body").get_attribute("data-input-seen"),
                "1",
            )
            browser.close()

    def test_safe_hh_edit_form_uses_native_submit_and_confirms_2xx(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()

            requests = []

            def handle(route):
                requests.append(route.request)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"ok": true}',
                )

            page.route(
                "**/applicant/vacancy_response/edit_ajax",
                handle,
            )
            page.set_content(
                """
                <form
                  id="cover-letter-123"
                  method="post"
                  action="https://hh.ru/applicant/vacancy_response/edit_ajax"
                >
                  <textarea name="text">prepared letter</textarea>
                  <input type="hidden" name="vacancy_id" value="123">
                  <button
                    id="submit"
                    type="submit"
                    data-qa="vacancy-response-letter-submit"
                    onclick="event.preventDefault()"
                  >
                    Отправить
                  </button>
                </form>
                """
            )
            submit = page.locator("#submit")

            result = dispatcher._hh_submit_post_apply_letter(
                page,
                submit,
                fallback=True,
            )

            self.assertTrue(result["confirmed"])
            self.assertEqual(result["mode"], "native-form-submit")
            self.assertEqual(result["status"], 200)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].method, "POST")
            self.assertIn("text=prepared+letter", requests[0].post_data or "")
            browser.close()

    def test_fallback_submit_uses_form_request_submit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <form id="letter-form">
                  <textarea id="letter">prepared letter</textarea>
                  <button
                    id="submit"
                    type="submit"
                    data-qa="vacancy-response-letter-submit"
                    onclick="event.preventDefault()"
                  >
                    Отправить
                  </button>
                </form>
                <script>
                  document.getElementById('letter-form').addEventListener('submit', (event) => {
                    event.preventDefault();
                    document.body.dataset.submitted = '1';
                  });
                </script>
                """
            )
            submit = page.locator("#submit")

            submit.click()
            self.assertIsNone(
                page.locator("body").get_attribute("data-submitted")
            )

            result = dispatcher._hh_submit_post_apply_letter(
                page,
                submit,
                fallback=True,
            )

            self.assertEqual(
                page.locator("body").get_attribute("data-submitted"),
                "1",
            )
            self.assertEqual(result["mode"], "requestSubmit")
            self.assertFalse(result["confirmed"])
            browser.close()


if __name__ == "__main__":
    unittest.main()
