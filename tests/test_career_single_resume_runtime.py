from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = (ROOT / "app" / "application_assets.py").read_text(encoding="utf-8")
YANDEX = (ROOT / "yandex_apply_worker.py").read_text(encoding="utf-8")
VK = (ROOT / "vk_apply_worker.py").read_text(encoding="utf-8")
TELEGRAM = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
DECISION = (ROOT / "app" / "decision_snapshot.py").read_text(encoding="utf-8")
DB = (ROOT / "app" / "db.py").read_text(encoding="utf-8")


class CareerSiteSingleResumeRuntimeTests(unittest.TestCase):
    def test_presentation_asset_is_removed_from_runtime(self) -> None:
        self.assertNotIn("PRESENTATION_PATH", ASSETS)
        self.assertNotIn("validate_application_assets", ASSETS)
        self.assertIn("CAREER_PROJECT_RESUME_PATH", ASSETS)

    def test_fixed_resume_filename_is_candidate_name(self) -> None:
        self.assertIn(
            'CAREER_PROJECT_RESUME_PATH = RESUMES_DIR / "Руденко Александр.pdf"',
            ASSETS,
        )

    def test_yandex_uses_fixed_project_resume(self) -> None:
        self.assertIn("validate_career_project_resume_asset", YANDEX)
        self.assertNotIn("application.selected_resume_key", YANDEX)
        self.assertNotIn("evaluation.selected_resume_key", YANDEX)

    def test_vk_uses_fixed_project_resume(self) -> None:
        self.assertIn("validate_career_project_resume_asset", VK)
        self.assertNotIn("application.selected_resume_key", VK)
        self.assertNotIn("evaluation.selected_resume_key", VK)

    def test_telegram_labels_career_resume_without_match_ranking(self) -> None:
        self.assertIn('is_career_site = vacancy_source in {"yandex", "vk", "tbank"}', TELEGRAM)
        self.assertIn('f"📄 Резюме: {CAREER_PROJECT_RESUME_TITLE}"', TELEGRAM)

    def test_approval_snapshot_rebinds_career_site_to_project(self) -> None:
        self.assertIn('elif vacancy_source in {"yandex", "vk", "tbank"}:', DECISION)
        self.assertIn("application.selected_resume_key = CAREER_PROJECT_RESUME_KEY", DECISION)
        self.assertIn("application.selected_resume_id = None", DECISION)

    def test_open_career_applications_are_backfilled(self) -> None:
        self.assertIn("def _backfill_career_project_resume_bindings", DB)
        self.assertIn("WHERE source IN ('yandex','vk','tbank')", DB)


if __name__ == "__main__":
    unittest.main()
