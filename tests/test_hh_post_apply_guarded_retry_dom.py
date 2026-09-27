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

    def test_external_form_attribute_uses_native_edit_submit(self):
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
                  id="cover-letter-ai-5606865584"
                  method="post"
                  action="https://hh.ru/applicant/vacancy_response/edit_ajax"
                >
                  <textarea name="text">prepared letter</textarea>
                  <input type="hidden" name="_xsrf" value="token">
                  <input type="hidden" name="topicId" value="5606865584">
                </form>
                <button
                  id="submit"
                  type="submit"
                  form="cover-letter-ai-5606865584"
                  data-qa="vacancy-response-letter-submit"
                >
                  Отправить
                </button>
                """
            )
            submit = page.locator("#submit")

            form = dispatcher._hh_associated_form(page, submit)
            self.assertIsNotNone(form)
            self.assertEqual(
                form.get_attribute("action"),
                "https://hh.ru/applicant/vacancy_response/edit_ajax",
            )

            result = dispatcher._hh_submit_post_apply_letter(
                page,
                submit,
                fallback=True,
            )

            self.assertTrue(result["confirmed"])
            self.assertEqual(result["mode"], "native-form-submit")
            self.assertEqual(len(requests), 1)
            self.assertIn("topicId=5606865584", requests[0].post_data or "")
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

    def test_response_card_snapshot_ignores_ordinary_chat_duplicates(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            letter = (
                "Здравствуйте!\n"
                "Мой основной профиль - управление IT-проектами и delivery полного цикла.\n"
                "С уважением,\nАлександр Руденко"
            )
            page.set_content(
                """
                <div data-qa="chatik-chat-message-100">
                  <div>Отклик на вакансию</div>
                  <div>Без сопроводительного письма</div>
                  <a data-qa="chatik-chat-message-applicant-action">
                    Добавить сопроводительное
                  </a>
                </div>
                <div data-qa="chatik-chat-message-101">
                  Мой основной профиль - управление IT-проектами и delivery полного цикла.
                </div>
                """
            )

            snapshot = dispatcher._hh_response_card_snapshot(
                page,
                letter,
            )

            self.assertEqual(snapshot["state"], "missing")
            self.assertIsNotNone(snapshot["action"])
            browser.close()

    def test_response_card_snapshot_confirms_letter_inside_original_response(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            letter = (
                "Здравствуйте!\n"
                "Мой основной профиль - управление IT-проектами и delivery полного цикла.\n"
                "С уважением,\nАлександр Руденко"
            )
            page.set_content(
                """
                <div data-qa="chatik-chat-message-100">
                  <div>Отклик на вакансию</div>
                  <div>Здравствуйте!</div>
                  <div>
                    Мой основной профиль - управление IT-проектами и delivery полного цикла.
                  </div>
                  <div>С уважением, Александр Руденко</div>
                </div>
                """
            )

            snapshot = dispatcher._hh_response_card_snapshot(
                page,
                letter,
            )

            self.assertEqual(snapshot["state"], "confirmed")
            browser.close()

    def test_response_card_action_must_enable_cover_letter_mode(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <a id="action" data-qa="chatik-chat-message-applicant-action">
                  Добавить сопроводительное
                </a>
                <div id="preview" hidden>
                  <div>Сопроводительное письмо</div>
                  <div>Введите текст сопроводительного письма</div>
                </div>
                <textarea data-qa="text-input" id="composer"></textarea>
                <script>
                  document.getElementById('action').addEventListener('click', () => {
                    document.getElementById('preview').hidden = false;
                  });
                </script>
                """
            )
            action = page.locator("#action")

            self.assertTrue(
                dispatcher._hh_activate_cover_mode(
                    page,
                    page,
                    action,
                )
            )
            self.assertTrue(dispatcher._hh_cover_mode_active(page))
            browser.close()


    def test_chatik_cover_editor_is_separate_from_ordinary_message_composer(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <div data-qa="chatik-chat-message-100">
                  <div>\u041e\u0442\u043a\u043b\u0438\u043a \u043d\u0430 \u0432\u0430\u043a\u0430\u043d\u0441\u0438\u044e</div>
                  <div id="missing">\u0411\u0435\u0437 \u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u043f\u0438\u0441\u044c\u043c\u0430</div>
                  <a id="action" data-qa="chatik-chat-message-applicant-action">
                    \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435
                  </a>
                </div>

                <div id="cover" hidden>
                  <div>\u0421\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435 \u043f\u0438\u0441\u044c\u043c\u043e</div>
                  <div>\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0442\u0435\u043a\u0441\u0442 \u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u043f\u0438\u0441\u044c\u043c\u0430</div>
                  <div id="cover-editor" role="textbox" contenteditable="true" data-qa="chatik-cover-letter-editor"></div>
                  <button id="save" type="button">\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c</button>
                </div>

                <textarea data-qa="text-input" placeholder="\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435" id="composer"></textarea>
                <button data-qa="chatik-do-send-message" id="send">\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c</button>

                <script>
                  const action = document.getElementById('action');
                  const cover = document.getElementById('cover');
                  const editor = document.getElementById('cover-editor');
                  const save = document.getElementById('save');
                  action.addEventListener('click', () => { cover.hidden = false; });
                  save.addEventListener('click', () => {
                    document.getElementById('missing').textContent = editor.innerText;
                    action.remove();
                    document.body.dataset.saved = '1';
                  });
                  document.getElementById('send').addEventListener('click', () => {
                    document.body.dataset.chatSent = '1';
                  });
                </script>
                """
            )
            letter = (
                "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435!\n"
                "\u041c\u043e\u0439 \u043e\u0441\u043d\u043e\u0432\u043d\u043e\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c - \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 IT-\u043f\u0440\u043e\u0435\u043a\u0442\u0430\u043c\u0438 \u0438 delivery \u043f\u043e\u043b\u043d\u043e\u0433\u043e \u0446\u0438\u043a\u043b\u0430.\n"
                "\u0421 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c,\n\u0410\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440 \u0420\u0443\u0434\u0435\u043d\u043a\u043e"
            )

            snapshot = dispatcher._hh_response_card_snapshot(page, letter)
            self.assertEqual(snapshot["state"], "missing")
            self.assertTrue(dispatcher._hh_activate_cover_mode(page, page, snapshot["action"]))

            editor = dispatcher._hh_cover_letter_editor(page)
            self.assertIsNotNone(editor)
            self.assertEqual(editor.get_attribute("id"), "cover-editor")
            self.assertNotEqual(editor.get_attribute("data-qa"), "text-input")

            editor.fill(letter)
            self.assertEqual(dispatcher._hh_editable_value(editor), letter)

            save = dispatcher._hh_cover_letter_save_button(page, editor)
            self.assertIsNotNone(save)
            self.assertEqual(save.inner_text(), dispatcher._HH_CHAT_COVER_SAVE)
            save.click()

            confirmed = dispatcher._hh_response_card_snapshot(page, letter)
            self.assertEqual(confirmed["state"], "confirmed")
            self.assertEqual(page.locator("#composer").input_value(), "")
            self.assertIsNone(page.locator("body").get_attribute("data-chat-sent"))
            self.assertEqual(page.locator("body").get_attribute("data-saved"), "1")
            browser.close()

    def test_chatik_cover_editor_never_falls_back_to_ordinary_composer(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(
                """
                <div>
                  <div>\u0421\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435 \u043f\u0438\u0441\u044c\u043c\u043e</div>
                  <div>\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0442\u0435\u043a\u0441\u0442 \u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u043f\u0438\u0441\u044c\u043c\u0430</div>
                </div>
                <textarea data-qa="text-input" placeholder="\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435"></textarea>
                <button data-qa="chatik-do-send-message">\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c</button>
                """
            )

            self.assertTrue(dispatcher._hh_cover_mode_active(page))
            self.assertIsNone(dispatcher._hh_cover_letter_editor(page))
            browser.close()


if __name__ == "__main__":
    unittest.main()
