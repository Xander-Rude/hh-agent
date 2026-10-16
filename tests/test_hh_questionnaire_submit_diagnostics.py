from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.hh_questionnaire_submit_diagnostics import (
    HHQuestionnairePostObservation,
    is_hh_questionnaire_post,
)


def _request(url="https://hh.ru/applicant/vacancy_response?vacancyId=138475784",
             method="POST", failure=None):
    return SimpleNamespace(url=url, method=method, failure=failure)


class QuestionnairePostDiagnosticsTests(unittest.TestCase):
    def test_only_native_hh_post_is_observed(self):
        self.assertTrue(is_hh_questionnaire_post(_request()))
        self.assertFalse(is_hh_questionnaire_post(_request(method="GET")))
        self.assertFalse(is_hh_questionnaire_post(_request(url="https://evil.example/applicant/vacancy_response")))
        self.assertFalse(is_hh_questionnaire_post(_request(url="https://hh.ru/login", method="POST")))
        self.assertFalse(is_hh_questionnaire_post(_request(url="http://hh.ru/applicant/vacancy_response")))
        self.assertFalse(is_hh_questionnaire_post(_request(url="https://hh.ru.evil.net/applicant/vacancy_response")))

    def test_http_rejection_preserved_without_payload_or_secret_url(self):
        request = _request("https://hh.ru/applicant/vacancy_response?vacancyId=138475784&token=secret")
        response = SimpleNamespace(
            request=request, status=422,
            headers={"location": "/applicant/negotiations?access_token=secret"},
        )
        observer = HHQuestionnairePostObservation()
        observer.response(response)
        note = observer.summary()
        self.assertIn("http_statuses=[422]", note)
        self.assertIn("redirect_paths=['/applicant/negotiations']", note)
        self.assertNotIn("secret", note)
        self.assertNotIn("138475784", note)

    def test_request_failed_has_only_browser_error_code(self):
        observer = HHQuestionnairePostObservation()
        observer.request_failed(_request(failure="net::ERR_CONNECTION_RESET https://hh.ru/?token=secret"))
        self.assertEqual(observer.request_failures, ["ERR_CONNECTION_RESET"])
        self.assertNotIn("secret", observer.summary())

    def test_irrelevant_http_response_is_ignored(self):
        observer = HHQuestionnairePostObservation()
        observer.response(SimpleNamespace(
            request=_request("https://hh.ru/api/another", "POST"),
            status=200, headers={},
        ))
        observer.response(SimpleNamespace(
            request=_request(method="GET"), status=302, headers={},
        ))
        self.assertEqual(observer.http_statuses, [])

    def test_visible_error_count_is_observational_only_and_redacted(self):
        class Page:
            def evaluate(self, script):
                self.script = script
                return {
                    "form_present": True, "submit_enabled": True,
                    "error_count": 1,
                    "hints": ["Укажите адрес candidate@example.com или телефон +79998887766."],
                    "invalid_names": ["known_field"],
                }

        observer = HHQuestionnairePostObservation()
        page = Page()
        ui = observer.visible_errors(page)
        self.assertEqual(ui["error_count"], 1)
        self.assertIn("[redacted]", ui["hints"][0])
        self.assertNotIn("candidate@example.com", observer.summary(ui))
        self.assertNotIn("+79998887766", observer.summary(ui))
        # DOM probing must be read-only; no submission or route operations.
        self.assertIn("querySelectorAll", page.script)
        self.assertNotIn(".click(", page.script)

    def test_inaccessible_page_does_not_erase_post_status(self):
        class BrokenPage:
            def evaluate(self, script):
                raise RuntimeError("navigation ongoing")

        observer = HHQuestionnairePostObservation()
        observer.response(SimpleNamespace(
            request=_request(), status=400, headers={},
        ))
        ui = observer.visible_errors(BrokenPage())
        self.assertEqual(ui["snapshot_error"], "read_failed")
        self.assertIn("http_statuses=[400]", observer.summary(ui))

    def test_network_response_is_not_submission_confirmation(self):
        observer = HHQuestionnairePostObservation()
        observer.response(SimpleNamespace(request=_request(), status=200, headers={}))
        self.assertEqual(observer.http_statuses, [200])
        # The diagnostic has no state-changing API: a 200 alone is not a
        # verified applied state. The HH UI must still independently confirm.
        self.assertFalse(hasattr(observer, "mark_applied"))


    def test_wait_keeps_request_alive_until_finished(self):
        """A slow POST must not trigger navigation merely after 1.6 seconds."""
        observer = HHQuestionnairePostObservation()

        class SlowResponsePage:
            def __init__(self):
                self.wait_calls = []

            def wait_for_timeout(self, ms):
                self.wait_calls.append(ms)
                # Simulate an HH response taking longer than old 1600-ms
                # timeout. We never navigate, retry, or submit here.
                if len(self.wait_calls) == 10:
                    observer.response(SimpleNamespace(
                        request=_request(), status=302, headers={
                            "location": "/applicant/negotiations?token=secret",
                        },
                    ))
                    observer.request_finished(_request())

        page = SlowResponsePage()
        result = observer.wait_for_terminal(
            page, post_seen=True, blocked=False, timeout_ms=30000,
        )
        self.assertEqual(result, "finished")
        self.assertEqual(len(page.wait_calls), 10)
        self.assertEqual(observer.http_statuses, [302])
        self.assertEqual(observer.finished_count, 1)
        self.assertNotIn("secret", observer.summary())
        self.assertFalse(hasattr(page, "goto"))
        self.assertFalse(hasattr(page, "click"))

    def test_wait_fails_closed_for_timeout_and_network_error(self):
        observer = HHQuestionnairePostObservation()

        class Page:
            def wait_for_timeout(self, ms):
                raise RuntimeError("page unavailable")

        self.assertEqual(
            observer.wait_for_terminal(Page(), post_seen=True, blocked=False),
            "page_unavailable",
        )
        self.assertEqual(
            observer.wait_for_terminal(Page(), post_seen=True, blocked=True),
            "blocked",
        )
        self.assertEqual(
            observer.wait_for_terminal(Page(), post_seen=False, blocked=False),
            "no_post",
        )
        self.assertEqual(
            observer.wait_for_terminal(Page(), post_seen=True, blocked=False, timeout_ms=0),
            "timeout",
        )
        observer.request_failed(_request(failure="net::ERR_CONNECTION_RESET"))
        self.assertEqual(
            observer.wait_for_terminal(Page(), post_seen=True, blocked=False),
            "failed",
        )

    def test_only_post_completion_matches_native_hh_submit(self):
        observer = HHQuestionnairePostObservation()
        observer.request_finished(_request(method="GET"))
        observer.request_finished(_request("https://example.com/applicant/vacancy_response"))
        self.assertEqual(observer.finished_count, 0)
        observer.request_finished(_request())
        self.assertEqual(observer.finished_count, 1)

    def test_form_validation_ignores_global_english_language_widget(self):
        """Regression for misleading ui_errors=4/English seen on MANGO."""
        import os
        if os.getenv("HH_APPLY_DOM_TESTS") != "1":
            self.skipTest("requires browser regression")
        from playwright.sync_api import sync_playwright

        html = """<html><body>
          <div role="alert" data-qa="header-validation-message">English</div>
          <form name="vacancy_response">
            <textarea name="task_1_text">example</textarea>
            <button type="submit">Откликнуться</button>
            <div data-qa="hidden-resume-warning" style="max-height:0px;overflow:hidden">
              <div role="alert">Видимость резюме</div>
            </div>
          </form>
        </body></html>"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html)
                observer = HHQuestionnairePostObservation()
                snapshot = observer.visible_errors(page)
                self.assertEqual(snapshot["error_count"], 0)
                self.assertEqual(snapshot["hints"], [])

                page.locator("form").evaluate(
                    "(f) => { const e=document.createElement('div');"
                    "e.setAttribute('role','alert'); e.innerText='Поле обязательно';"
                    "f.appendChild(e); }"
                )
                later = observer.visible_errors(page)
                self.assertEqual(later["error_count"], 1)
                self.assertIn("Поле обязательно", later["hints"])
            finally:
                browser.close()


    def test_native_form_preflight_rejects_missing_fields_without_sending(self):
        from app.hh_questionnaire_submit_diagnostics import native_form_preflight

        class Page:
            def __init__(self, valid):
                self.valid = valid
                self.checked = False

            def evaluate(self, script, args):
                self.checked = True
                self.assert_script = script
                self.received = args
                return self.valid

        p = Page(False)
        self.assertFalse(native_form_preflight(p, {"task_123": "yes"}, "Cover letter"))
        self.assertTrue(p.checked)
        self.assertNotIn("fetch(", p.assert_script)
        self.assertNotIn(".submit(", p.assert_script)
        self.assertEqual(p.received["fields"], {"task_123": "yes"})
        self.assertFalse(native_form_preflight(Page(True), {}, "Letter"))
        self.assertFalse(native_form_preflight(Page(True), {"task_123": "yes"}, ""))

    def test_dom_preflight_matches_radio_text_and_unnamed_letter(self):
        import os
        if os.environ.get("HH_APPLY_DOM_TESTS") != "1":
            self.skipTest("browser regression required")
        from playwright.sync_api import sync_playwright
        from app.hh_questionnaire_submit_diagnostics import native_form_preflight

        html = """<form name="vacancy_response">
          <input type="radio" name="task_123" value="yes" checked><span>Yes</span>
          <input type="radio" name="task_123" value="no"><span>No</span>
          <textarea name="task_456_text">350000</textarea>
          <textarea data-qa="vacancy-response-popup-form-letter-input">Prepared letter</textarea>
          <button type="submit">Send</button>
        </form>"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html)
                expected = {"task_123": "yes", "task_456_text": "350000"}
                self.assertTrue(native_form_preflight(page, expected, "Prepared letter"))
                self.assertFalse(native_form_preflight(page, {**expected, "task_123": "no"}, "Prepared letter"))
                self.assertFalse(native_form_preflight(page, expected, "Wrong letter"))
                page.locator('textarea[name="task_456_text"]').fill("")
                self.assertFalse(native_form_preflight(page, expected, "Prepared letter"))
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
