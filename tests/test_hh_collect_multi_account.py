from pathlib import Path

import hh_collect


ROOT = Path(__file__).resolve().parents[1]


def test_default_collector_uses_old_profile() -> None:
    assert hh_collect.COLLECT_ACCOUNT_KEY == "old"
    assert Path(hh_collect.PROFILE_DIR).name == "browser-profile"


def test_application_history_clause_is_account_scoped() -> None:
    clean_clause = str(hh_collect._application_account_clause("clean"))
    old_clause = str(hh_collect._application_account_clause("old"))

    assert "applications.account_key" in clean_clause
    assert "applications.account_key" in old_clause
    assert "IS NULL" in old_clause


def test_clean_profile_and_search_are_configurable_per_process() -> None:
    source = (ROOT / "hh_collect.py").read_text(encoding="utf-8")

    assert 'os.getenv("HH_COLLECT_ACCOUNT")' in source
    assert "get_account(COLLECT_ACCOUNT_KEY)" in source
    assert "COLLECT_ACCOUNT.profile_dir" in source
    assert "HH_ALWAYS_RUN_TARGET_SEARCH" in source
    assert "ALWAYS_RUN_TARGET_SEARCH" in source


def test_existing_clean_vacancy_does_not_reuse_old_response_cache() -> None:
    source = (ROOT / "hh_collect.py").read_text(encoding="utf-8")

    assert 'COLLECT_ACCOUNT_KEY == "old"' in source
    assert "account_key=COLLECT_ACCOUNT_KEY" in source
