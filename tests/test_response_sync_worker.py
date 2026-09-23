import unittest
from unittest.mock import Mock

import response_sync_worker as worker


class ResponseSyncFilterTests(unittest.TestCase):
    def test_filter_fallbacks_preserve_workflow_semantics(self):
        self.assertEqual(
            worker.FILTER_STATUS_FALLBACK["response"],
            "submitted",
        )
        self.assertEqual(
            worker.FILTER_STATUS_FALLBACK["invitations"],
            "workflow_invited",
        )
        self.assertEqual(
            worker.FILTER_STATUS_FALLBACK["discard"],
            "workflow_discarded",
        )

    def test_rejects_overlapping_status_filter_sets(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "status filter is not trustworthy",
        ):
            worker._validate_status_filter_sets(
                {
                    "response": {"1", "2", "3"},
                    "invitations": {"1", "2", "3"},
                    "discard": {"1", "2", "3"},
                }
            )

    def test_allows_disjoint_status_filter_sets(self):
        worker._validate_status_filter_sets(
            {
                "response": {"1", "2"},
                "invitations": {"3"},
                "discard": {"4", "5"},
            }
        )

    def test_builds_status_and_page_query(self):
        original = worker.NEGOTIATIONS_URL
        try:
            worker.NEGOTIATIONS_URL = (
                "https://hh.ru/applicant/negotiations?foo=bar"
            )
            self.assertEqual(
                worker._negotiations_page_url("discard", 3),
                "https://hh.ru/applicant/negotiations?foo=bar&status=discard&page=3",
            )
        finally:
            worker.NEGOTIATIONS_URL = original

    def test_discard_fallback_is_not_an_explicit_rejection(self):
        anchor = Mock()
        anchor.get_attribute.return_value = "/vacancy/777"
        anchor.evaluate.return_value = []
        anchor.inner_text.return_value = "Example vacancy"

        anchors = Mock()
        anchors.count.return_value = 1
        anchors.nth.return_value = anchor

        page = Mock()
        page.locator.return_value = anchors

        result = worker._extract_page_states(
            page,
            status_filter="discard",
        )

        self.assertEqual(
            result["777"]["status"],
            "workflow_discarded",
        )

    def test_extract_uses_filter_fallback_when_card_has_no_status_text(self):
        anchor = Mock()
        anchor.get_attribute.return_value = "/vacancy/123456"
        anchor.evaluate.return_value = []
        anchor.inner_text.return_value = "Example vacancy"

        anchors = Mock()
        anchors.count.return_value = 1
        anchors.nth.return_value = anchor

        page = Mock()
        page.locator.return_value = anchors

        result = worker._extract_page_states(
            page,
            status_filter="invitations",
        )

        self.assertEqual(
            result["123456"]["status"],
            "workflow_invited",
        )


if __name__ == "__main__":
    unittest.main()
