import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import response_sync_worker as worker


class ResponseSyncProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = Mock()
        self.page.goto.return_value = SimpleNamespace(status=200)
        self.item = {
            "application_id": 42,
            "hh_id": "123456",
            "url": "https://hh.ru/vacancy/123456",
            "title": "IT Project Manager",
        }

    def test_unresolved_page_is_not_safe_for_no_response_derivation(self):
        with (
            patch.object(worker, "hh_is_authenticated", return_value=True),
            patch.object(
                worker,
                "detect_hh_vacancy_career_state",
                return_value=(None, ""),
            ),
            patch.object(worker, "update_career_status") as update,
        ):
            checked, state = worker._probe_application(
                self.page,
                account_key="clean",
                item=self.item,
            )

        self.assertFalse(checked)
        self.assertIsNone(state)
        update.assert_not_called()

    def test_submitted_only_refreshes_materialized_state(self):
        with (
            patch.object(worker, "hh_is_authenticated", return_value=True),
            patch.object(
                worker,
                "detect_hh_vacancy_career_state",
                return_value=("submitted", "Отклик отправлен"),
            ),
            patch.object(worker, "update_career_status") as update,
        ):
            checked, state = worker._probe_application(
                self.page,
                account_key="clean",
                item=self.item,
            )

        self.assertTrue(checked)
        self.assertEqual(state, "submitted")
        kwargs = update.call_args.kwargs
        self.assertFalse(kwargs["emit_event"])
        self.assertEqual(kwargs["confidence"], "platform_observed")

    def test_explicit_rejection_emits_platform_outcome(self):
        with (
            patch.object(worker, "hh_is_authenticated", return_value=True),
            patch.object(
                worker,
                "detect_hh_vacancy_career_state",
                return_value=("rejected", "Работодатель отказал"),
            ),
            patch.object(worker, "update_career_status") as update,
        ):
            checked, state = worker._probe_application(
                self.page,
                account_key="clean",
                item=self.item,
            )

        self.assertTrue(checked)
        self.assertEqual(state, "rejected")
        kwargs = update.call_args.kwargs
        self.assertNotIn("emit_event", kwargs)
        self.assertEqual(kwargs["confidence"], "platform_observed")
        self.assertEqual(
            kwargs["raw_ref"],
            "hh-vacancy:clean:123456",
        )

    def test_http_error_is_not_safe_for_no_response_derivation(self):
        self.page.goto.return_value = SimpleNamespace(status=404)

        with (
            patch.object(worker, "hh_is_authenticated", return_value=True),
            patch.object(
                worker,
                "detect_hh_vacancy_career_state",
            ) as detect,
        ):
            checked, state = worker._probe_application(
                self.page,
                account_key="clean",
                item=self.item,
            )

        self.assertFalse(checked)
        self.assertIsNone(state)
        detect.assert_not_called()

    def test_login_redirect_is_not_safe_for_no_response_derivation(self):
        self.page.url = "https://hh.ru/account/login"

        checked, state = worker._probe_application(
            self.page,
            account_key="clean",
            item=self.item,
        )

        self.assertFalse(checked)
        self.assertEqual(state, "session_lost")


class ResponseSyncAccountTests(unittest.TestCase):
    def test_no_response_is_scoped_to_successfully_checked_applications(self):
        candidates = [
            {
                "application_id": 1,
                "hh_id": "1",
                "url": "https://hh.ru/vacancy/1",
                "title": "One",
            },
            {
                "application_id": 2,
                "hh_id": "2",
                "url": "https://hh.ru/vacancy/2",
                "title": "Two",
            },
        ]

        page = Mock()
        context = Mock()
        context.pages = [page]
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = context

        account = SimpleNamespace(
            key="clean",
            label="CLEAN",
            profile_dir="C:/fake-profile",
        )

        with (
            patch.object(
                worker,
                "_candidate_applications",
                return_value=candidates,
            ),
            patch.object(
                worker,
                "hh_is_authenticated",
                return_value=True,
            ),
            patch.object(
                worker,
                "_probe_application",
                side_effect=[
                    (True, None),
                    (False, None),
                ],
            ),
            patch.object(
                worker,
                "record_due_no_response_events",
                return_value={
                    "no_response_7d": 0,
                    "no_response_30d": 0,
                },
            ) as no_response,
        ):
            total, checked, code = worker._sync_account(
                playwright,
                account,
            )

        self.assertEqual((total, checked, code), (2, 1, 0))
        no_response.assert_called_once_with(
            application_ids={1},
            hh_only=True,
        )


class ResponseSyncSourceSafetyTests(unittest.TestCase):
    def test_legacy_negotiation_filter_inference_is_removed(self):
        source = open(
            worker.__file__,
            "r",
            encoding="utf-8",
        ).read()
        self.assertNotIn("FILTER_STATUS_FALLBACK", source)
        self.assertNotIn("_validate_status_filter_sets", source)
        self.assertNotIn("NEGOTIATIONS_URL", source)


if __name__ == "__main__":
    unittest.main()
