import unittest

from hh_response_state import (
    detect_existing_hh_response,
    existing_response_marker_from_text,
)


class _FakeItem:
    def __init__(self, *, visible=True, text=""):
        self.visible = visible
        self.text = text

    def is_visible(self):
        return self.visible

    def inner_text(self, timeout=None):
        return self.text


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


if __name__ == "__main__":
    unittest.main()
