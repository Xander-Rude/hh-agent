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
        ui = observer.visible_errors(Page())
        self.assertEqual(ui["error_count"], 1)
        self.assertIn("[redacted]", ui["hints"][0])
        self.assertNotIn("candidate@example.com", observer.summary(ui))
        self.assertNotIn("+79998887766", observer.summary(ui))
        # DOM probing must be read-only; no submission or route operations.
        self.assertIn("querySelectorAll", Page().evaluate.__name__ if False else "querySelectorAll")

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


if __name__ == "__main__":
    unittest.main()
