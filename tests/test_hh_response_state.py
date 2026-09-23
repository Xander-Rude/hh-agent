import unittest

from hh_response_state import (
    classify_hh_negotiation_text,
    detect_existing_hh_response,
    detect_hh_vacancy_career_state,
    existing_response_marker_from_text,
)


class _FakeItem:
    def __init__(self, *, visible=True, text="", evaluated_texts=None):
        self.visible = visible
        self.text = text
        self.evaluated_texts = evaluated_texts or []

    def is_visible(self):
        return self.visible

    def inner_text(self, timeout=None):
        return self.text

    def evaluate(self, script):
        return self.evaluated_texts


class _FakeLocator:
    def __init__(self, items=None, evaluated_text=""):
        self.items = items or []
        self.evaluated_text = evaluated_text

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def evaluate(self, script):
        return self.evaluated_text


class _FakePage:
    def __init__(self, selectors=None, body_text=""):
        self.selectors = selectors or {}
        self.body_text = body_text

    def locator(self, selector):
        if selector == "body":
            return _FakeLocator(evaluated_text=self.body_text)
        return _FakeLocator(items=self.selectors.get(selector, []))


class ExistingResponseMarkerTests(unittest.TestCase):
    def test_detects_previous_application(self):
        self.assertEqual(
            existing_response_marker_from_text(
                "Спасибо. Вы уже откликнулись на эту вакансию."
            ),
            "вы уже откликнулись",
        )

    def test_detects_employer_rejection(self):
        self.assertEqual(
            existing_response_marker_from_text(
                "Работодатель отклонил ваш отклик."
            ),
            "работодатель отклонил ваш отклик",
        )

    def test_does_not_match_generic_vacancy_copy(self):
        self.assertIsNone(
            existing_response_marker_from_text(
                "Ждем ваш отклик. Работодатель рассматривает кандидатов."
            )
        )

    def test_response_specific_selector_is_enough(self):
        selector = '[data-qa^="responded-"]'
        page = _FakePage(
            selectors={
                selector: [_FakeItem(text="")]
            }
        )

        self.assertEqual(
            detect_existing_hh_response(page),
            f"selector:{selector}",
        )

    def test_text_fallback_uses_page_without_description(self):
        page = _FakePage(
            body_text="Вам отказали по этой вакансии."
        )

        self.assertEqual(
            detect_existing_hh_response(page),
            "вам отказали",
        )


class VacancyCareerStateTests(unittest.TestCase):
    def test_viewed_requires_response_widget_evidence(self):
        selector = '[data-qa^="responded-"]'
        page = _FakePage(
            selectors={
                selector: [
                    _FakeItem(
                        evaluated_texts=[
                            "Отклик просмотрен работодателем"
                        ]
                    )
                ]
            }
        )

        state, evidence = detect_hh_vacancy_career_state(page)
        self.assertEqual(state, "viewed")
        self.assertIn("просмотрен", evidence.lower())

    def test_response_widget_rejection_beats_invitation(self):
        selector = '[data-qa*="vacancy-response"]'
        page = _FakePage(
            selectors={
                selector: [
                    _FakeItem(
                        evaluated_texts=[
                            "Приглашение закрыто. Работодатель отказал."
                        ]
                    )
                ]
            }
        )

        state, _ = detect_hh_vacancy_career_state(page)
        self.assertEqual(state, "rejected")

    def test_generic_body_rejection_is_not_trusted_as_outcome_or_no_response(self):
        page = _FakePage(
            body_text="Вам отказали по этой вакансии."
        )

        self.assertEqual(
            detect_hh_vacancy_career_state(page),
            (None, ""),
        )

    def test_neutral_existing_response_marker_allows_submitted(self):
        page = _FakePage(
            body_text="Вы уже откликнулись на эту вакансию."
        )

        state, evidence = detect_hh_vacancy_career_state(page)
        self.assertEqual(state, "submitted")
        self.assertEqual(evidence, "вы уже откликнулись")

    def test_no_response_evidence_returns_none(self):
        page = _FakePage(
            body_text="Ждем ваш отклик."
        )

        self.assertEqual(
            detect_hh_vacancy_career_state(page),
            (None, ""),
        )


class NegotiationStateTests(unittest.TestCase):
    def test_invitation_is_workflow_not_interview(self):
        self.assertEqual(
            classify_hh_negotiation_text(
                "Работодатель пригласил вас на следующий этап"
            ),
            "workflow_invited",
        )

    def test_rejection_wins_over_invitation_wording(self):
        self.assertEqual(
            classify_hh_negotiation_text(
                "Приглашение закрыто. Работодатель отказал."
            ),
            "rejected",
        )

    def test_viewed_is_separate_state(self):
        self.assertEqual(
            classify_hh_negotiation_text(
                "Отклик просмотрен работодателем"
            ),
            "viewed",
        )


if __name__ == "__main__":
    unittest.main()
