from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "ozon_apply_worker.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
BACKGROUND = (ROOT / "background_apply.py").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "collect_careers.py").read_text(encoding="utf-8")


class OzonApplyRuntimeTests(unittest.TestCase):
    def test_worker_requires_explicit_approval(self) -> None:
        self.assertIn(
            "if not approved_for_dispatch(application):",
            WORKER,
        )
        self.assertIn(
            'Application.status == "approved"',
            WORKER,
        )

    def test_worker_never_blind_submits_unverified_ozon_form(self) -> None:
        self.assertIn("ANTIBOT_MARKERS", WORKER)
        self.assertIn("production egress", WORKER)
        self.assertIn(
            "форма отклика ещё не верифицирована",
            WORKER,
        )
        self.assertNotIn("submit.click(", WORKER)
        self.assertNotIn("set_input_files(", WORKER)

    def test_dispatcher_routes_ozon_approved_queue(self) -> None:
        self.assertIn("import ozon_apply_worker", DISPATCHER)
        self.assertIn("def load_ozon_queue_approved", DISPATCHER)
        self.assertIn('source="ozon"', DISPATCHER)
        self.assertIn("worker=ozon_apply_worker", DISPATCHER)

    def test_scheduled_external_dispatch_handles_ozon(self) -> None:
        self.assertIn('"OZON_APPLY_LIVE": "true"', BACKGROUND)
        self.assertIn('"OZON_APPLY_HEADLESS": "true"', BACKGROUND)

    def test_career_collector_registers_ozon(self) -> None:
        self.assertIn("OzonSource", COLLECTOR)
        self.assertIn("OzonSource()", COLLECTOR)


if __name__ == "__main__":
    unittest.main()
