from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app import resume_matcher, resume_selector


class SingleResumePolicyTests(unittest.TestCase):
    def _config_path(self, temp_dir: str) -> Path:
        path = Path(temp_dir) / "resumes.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "resumes": {
                        "project": {
                            "title": "Руководитель проектов",
                            "hh_resume_id": "project-resume-id",
                        },
                        "delivery": {
                            "title": "Delivery Manager",
                            "hh_resume_id": "delivery-resume-id",
                        },
                    },
                    "generated_resumes": [],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return path

    def test_matcher_always_returns_project_without_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(temp_dir)
            with patch.object(resume_matcher, "RESUMES_PATH", path):
                result = resume_matcher.match_resume(
                    vacancy_title="Delivery Manager",
                    vacancy_description="Product delivery role",
                    vacancy_score=99,
                )

        self.assertEqual(result.action, "use_existing")
        self.assertEqual(result.selected_resume_key, "project")
        self.assertEqual(result.selected_resume_title, "Руководитель проектов")
        self.assertEqual(result.selected_resume_id, "project-resume-id")
        self.assertEqual(result.match_score, 0)
        self.assertEqual(len(result.scores), 1)

    def test_legacy_selector_compatibility_also_returns_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(temp_dir)
            with patch.object(resume_selector, "CONFIG_PATH", path):
                result = resume_selector.choose_resume(
                    vacancy_title="Product Owner",
                    vacancy_description="Product role",
                    vacancy_score=95,
                )

        self.assertEqual(result.action, "use_existing")
        self.assertEqual(result.selected_resume_key, "project")
        self.assertEqual(result.selected_resume_title, "Руководитель проектов")
        self.assertEqual(result.selected_resume_id, "project-resume-id")


if __name__ == "__main__":
    unittest.main()
