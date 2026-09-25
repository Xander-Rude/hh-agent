from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.external_apply_policy import (
    approved_for_dispatch,
    resolve_application_text,
)


ROOT = Path(__file__).resolve().parents[1]
YANDEX = (ROOT / "yandex_apply_worker.py").read_text(encoding="utf-8")
VK = (ROOT / "vk_apply_worker.py").read_text(encoding="utf-8")
TBANK = (ROOT / "tbank_apply_worker.py").read_text(encoding="utf-8")


class ExternalApprovalAuthorityTests(unittest.TestCase):
    def test_only_application_approved_status_authorizes_dispatch(self) -> None:
        self.assertTrue(
            approved_for_dispatch(
                SimpleNamespace(
                    status="approved",
                    cover_letter="",
                )
            )
        )
        for status in (
            "notified",
            "review",
            "manual_required",
            "applying",
            "skipped",
        ):
            self.assertFalse(
                approved_for_dispatch(
                    SimpleNamespace(
                        status=status,
                        cover_letter="",
                    )
                )
            )

    def test_application_text_wins_over_later_evaluation(self) -> None:
        application = SimpleNamespace(
            status="approved",
            cover_letter="Approved snapshot text",
        )
        evaluation = SimpleNamespace(
            decision="reject",
            cover_letter="Later evaluation text",
        )

        self.assertEqual(
            resolve_application_text(application, evaluation),
            "Approved snapshot text",
        )

    def test_evaluation_text_is_only_fallback_not_a_decision_gate(self) -> None:
        application = SimpleNamespace(
            status="approved",
            cover_letter=None,
        )
        evaluation = SimpleNamespace(
            decision="review",
            cover_letter="Fallback text",
        )

        self.assertEqual(
            resolve_application_text(application, evaluation),
            "Fallback text",
        )

    def test_external_workers_use_the_same_approval_policy(self) -> None:
        for source in (YANDEX, VK, TBANK):
            self.assertIn(
                "if not approved_for_dispatch(application):",
                source,
            )
            self.assertNotIn(
                "evaluation.decision",
                source,
            )
        for source in (YANDEX, VK):
            self.assertIn(
                "resolve_application_text(",
                source,
            )

    def test_all_queues_are_already_approved_only(self) -> None:
        self.assertIn(
            'Application.status == "approved"',
            YANDEX,
        )
        self.assertIn(
            'Application.status == "approved"',
            VK,
        )
        self.assertIn(
            'Application.status == "approved"',
            TBANK,
        )


if __name__ == "__main__":
    unittest.main()
