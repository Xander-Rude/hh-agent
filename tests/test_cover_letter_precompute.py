import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREP = (ROOT / "prepare_cover_letters.py").read_text(encoding="utf-8")
BOT = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
PENDING = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")
PIPELINE = (ROOT / "background_pipeline.py").read_text(encoding="utf-8")


class CoverLetterPrecomputeTests(unittest.TestCase):
    def test_writer_runs_before_new_not_inside_new(self) -> None:
        self.assertIn("write_human_cover_letter(", PREP)
        self.assertNotIn("write_human_cover_letter", BOT)
        self.assertNotIn("_schedule_cover_upgrade(", PENDING)

    def test_old_and_clean_store_writer_version(self) -> None:
        self.assertGreaterEqual(
            PREP.count("cover_letter_version = COVER_WRITER_VERSION"),
            2,
        )

    def test_pipeline_runs_precompute_stage(self) -> None:
        self.assertIn("prepare_cover_letters.py", PIPELINE)
        self.assertIn("cover_letter_prepare.log", PIPELINE)

    def test_precompute_has_final_similarity_guard(self) -> None:
        self.assertIn("MAX_FINAL_SIMILARITY", PREP)
        self.assertIn("_acceptable_final", PREP)


if __name__ == "__main__":
    unittest.main()
