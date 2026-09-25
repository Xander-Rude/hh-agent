from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "tbank_apply_worker.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
BACKGROUND = (ROOT / "background_apply.py").read_text(encoding="utf-8")


class TBankApplyWorkerRuntimeTests(unittest.TestCase):
    def test_worker_requires_explicit_approval(self) -> None:
        self.assertIn(
            "if not approved_for_dispatch(application):",
            WORKER,
        )
        self.assertIn(
            'Application.status == "approved"',
            WORKER,
        )

    def test_worker_fills_observed_tbank_form_fields(self) -> None:
        for marker in (
            '"name": _fill_named(',
            '"city": fill_city(',
            '"email": _fill_named(',
            '"phone": _fill_named(',
            'input[type="file"]',
            'button[type="submit"][name="submit"]',
        ):
            self.assertIn(marker, WORKER)

    def test_hidden_native_file_input_is_supported(self) -> None:
        start = WORKER.index("def upload_resume(")
        end = WORKER.index("\n\ndef fill_optional_social_link", start)
        upload = WORKER[start:end]

        self.assertIn(
            "inputs = page.locator('input[type=\"file\"]')",
            upload,
        )
        self.assertNotIn("_first_visible(", upload)
        self.assertIn("set_input_files(", upload)

    def test_worker_does_not_fill_resume_url_or_portfolio(self) -> None:
        self.assertNotIn(
            'resumeAndPortfolioLink_resume',
            WORKER,
        )
        self.assertNotIn(
            "Добавить портфолио",
            WORKER,
        )
        self.assertNotIn(
            "presentation",
            WORKER.lower(),
        )

    def test_submit_is_conservative_after_click(self) -> None:
        self.assertIn(
            "Повторно автоматически НЕ отправлять.",
            WORKER,
        )
        self.assertIn(
            "confirm_success(page)",
            WORKER,
        )
        self.assertIn(
            '"manual_required"',
            WORKER,
        )

    def test_dispatcher_includes_tbank_queue(self) -> None:
        self.assertIn("import tbank_apply_worker", DISPATCHER)
        self.assertIn("def load_tbank_queue_approved", DISPATCHER)
        self.assertIn('source="tbank"', DISPATCHER)
        self.assertIn("worker=tbank_apply_worker", DISPATCHER)

    def test_scheduled_apply_enables_tbank_once_with_external_dispatch(self) -> None:
        self.assertIn('"TBANK_APPLY_LIVE": "true"', BACKGROUND)
        self.assertIn('"TBANK_APPLY_HEADLESS": "true"', BACKGROUND)
        self.assertIn('"APPLY_DISPATCH_EXTERNAL"', BACKGROUND)


if __name__ == "__main__":
    unittest.main()
