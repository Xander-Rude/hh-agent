from pathlib import Path

from telegram_hard_filter_appeal_patch import APPEAL_MODEL_SUFFIX


ROOT = Path(__file__).resolve().parents[1]


def test_appeal_model_suffix_is_stable():
    assert APPEAL_MODEL_SUFFIX == "+hard-filter-appeal"


def test_telegram_entrypoint_installs_appeal_patch():
    source = (ROOT / "telegram_bot_entry.py").read_text(
        encoding="utf-8"
    )
    assert "install_appeal_patch(telegram_bot)" in source


def test_appeal_patch_preserves_original_new_workflow():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert "await original_send(" in source
    assert "_send_with_retry" in source
    assert "Evaluation.model.endswith" in source
    assert "APPEAL_MODEL_SUFFIX" in source


def test_appeal_patch_does_not_require_normal_score_threshold():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert "Evaluation.model.endswith" in source
    assert "MIN_SCORE_TO_NOTIFY" not in source
