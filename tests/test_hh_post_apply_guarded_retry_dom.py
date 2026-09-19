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

            dispatcher._hh_submit_post_apply_letter(
                submit,
                fallback=True,
            )

            self.assertEqual(
                page.locator("body").get_attribute("data-submitted"),
                "1",
            )
            browser.close()

    def test_chat_fallback_delivers_letter_from_exact_vacancy(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            html = """
                <div>Вы откликнулись</div>
                <button data-qa="vacancy-response-link-view-topic" id="topic">
                  Чат
                </button>
                <div id="chat" hidden>
                  <div id="thread"></div>
                  <textarea data-qa="text-input" id="composer"></textarea>
                  <button data-qa="chatik-do-send-message" id="send" disabled>
                    Отправить
                  </button>
                </div>
                <script>
                  const topic = document.getElementById('topic');
                  const chat = document.getElementById('chat');
                  const composer = document.getElementById('composer');
                  const send = document.getElementById('send');
                  const thread = document.getElementById('thread');

                  topic.addEventListener('click', () => {
                    chat.hidden = false;
                  });
                  composer.addEventListener('input', () => {
                    send.disabled = !composer.value;
                  });
                  send.addEventListener('click', () => {
                    thread.textContent = composer.value;
                    composer.value = '';
                    send.disabled = true;
                  });
                </script>
            """

            page.route(
                "https://hh.ru/vacancy/1",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=html,
                ),
            )
            page.goto("https://hh.ru/vacancy/1")

            letter = (
                "Здравствуйте!\n"
                "У меня релевантный опыт управления продуктами и IT-проектами.\n"
                "Буду рад обсудить детали."
            )
            delivered, reason = dispatcher._hh_deliver_cover_letter_via_chat(
                page,
                letter,
            )

            self.assertTrue(delivered, reason)
            self.assertIn(
                "релевантный опыт управления продуктами",
                page.locator("#thread").inner_text(),
            )
            self.assertEqual(page.locator("#composer").input_value(), "")
            browser.close()

    def test_chat_fallback_is_idempotent_when_letter_already_in_thread(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            letter = (
                "Здравствуйте!\n"
                "У меня релевантный опыт управления продуктами и IT-проектами.\n"
                "Буду рад обсудить детали."
            )
            html = f"""
                <div>Вы откликнулись</div>
                <button data-qa="vacancy-response-link-view-topic" id="topic">
                  Чат
                </button>
                <div id="chat" hidden>
                  <div id="thread">{letter}</div>
                  <textarea data-qa="text-input" id="composer"></textarea>
                  <button data-qa="chatik-do-send-message" id="send">
                    Отправить
                  </button>
                </div>
                <script>
                  document.getElementById('topic').addEventListener('click', () => {{
                    document.getElementById('chat').hidden = false;
                  }});
                  document.getElementById('send').addEventListener('click', () => {{
                    document.body.dataset.sentAgain = '1';
                  }});
                </script>
            """

            page.route(
                "https://hh.ru/vacancy/2",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=html,
                ),
            )
            page.goto("https://hh.ru/vacancy/2")

            delivered, reason = dispatcher._hh_deliver_cover_letter_via_chat(
                page,
                letter,
            )

            self.assertTrue(delivered, reason)
            self.assertIsNone(
                page.locator("body").get_attribute("data-sent-again")
            )
            browser.close()


if __name__ == "__main__":
    unittest.main()
