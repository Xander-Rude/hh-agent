import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import apply_worker as worker


class OutcomeApplySemanticsTests(unittest.TestCase):
    def test_preexisting_hh_application_is_not_counted_as_new_apply(self):
        application = SimpleNamespace(
            id=42,
            vacancy_id=5097,
            account_key="clean",
            cover_letter="",
        )

        with (
            patch.object(worker, "record_outcome_event") as record_outcome,
            patch.object(worker, "set_status") as set_status,
        ):
            result = worker.finalize_existing_application(
                Mock(),
                application,
                preexisting=True,
            )

        self.assertEqual(result, "applied")
        record_outcome.assert_called_once()
        args, kwargs = record_outcome.call_args
        self.assertEqual(args[:2], (42, "already_applied"))
        self.assertEqual(kwargs["confidence"], "platform_observed")
        set_status.assert_called_once_with(
            42,
            "applied",
            applied=True,
            emit_outcome=False,
        )

    def test_ambiguous_current_submit_can_still_count_as_applied(self):
        application = SimpleNamespace(
            id=43,
            vacancy_id=5098,
            account_key="clean",
            cover_letter="",
        )

        with (
            patch.object(worker, "record_outcome_event") as record_outcome,
            patch.object(worker, "set_status") as set_status,
        ):
            result = worker.finalize_existing_application(
                Mock(),
                application,
                preexisting=False,
            )

        self.assertEqual(result, "applied")
        record_outcome.assert_not_called()
        set_status.assert_called_once_with(
            43,
            "applied",
            applied=True,
            emit_outcome=True,
        )


if __name__ == "__main__":
    unittest.main()
