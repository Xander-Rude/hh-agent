import asyncio
from pathlib import Path
from types import SimpleNamespace

from telegram_hard_filter_appeal_patch import (
    APPEAL_ACCOUNT_KEY,
    APPEAL_MODEL_SUFFIX,
    install,
)


ROOT = Path(__file__).resolve().parents[1]


def test_appeal_model_suffix_is_stable():
    assert APPEAL_MODEL_SUFFIX == "+hard-filter-appeal"


def test_appeal_account_is_old():
    assert APPEAL_ACCOUNT_KEY == "old"


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
    assert "account_key=account_key" in source
    assert "_send_with_retry" in source
    assert "Evaluation.model.endswith" in source
    assert "APPEAL_MODEL_SUFFIX" in source


def test_appeal_patch_does_not_require_normal_score_threshold():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert "Evaluation.model.endswith" in source
    assert "MIN_SCORE_TO_NOTIFY" not in source


def test_appeal_patch_requires_recommended_decision():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert "RECOMMENDED_DECISIONS" in source
    assert "Evaluation.decision.in_(" in source


def test_clean_request_is_forwarded_but_skips_old_appeals():
    calls = []

    async def original_send(context, chat_id=None, account_key=None):
        calls.append((chat_id, account_key))

    module = SimpleNamespace(send_new_vacancies=original_send)
    install(module)

    asyncio.run(
        module.send_new_vacancies(
            SimpleNamespace(),
            chat_id=123,
            account_key="clean",
        )
    )

    assert calls == [(123, "clean")]


def test_appeal_cards_use_application_scoped_callbacks():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert "account_key=APPEAL_ACCOUNT_KEY" in source
    assert "application_id=state.id" in source


def test_recommended_old_appeal_routes_to_manual_queue():
    source = (
        ROOT / "telegram_hard_filter_appeal_patch.py"
    ).read_text(encoding="utf-8")
    assert 'state.status = "manual_required"' in source
    assert "build_manual_required_message" in source
    assert "build_manual_required_keyboard" in source
