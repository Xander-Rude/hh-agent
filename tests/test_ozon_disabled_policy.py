from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_career_collector_disables_ozon_by_default() -> None:
    source = _source("collect_careers.py")

    assert 'os.getenv("OZON_ENABLED", "false")' in source
    assert "if OZON_ENABLED:" in source
    assert "SOURCES.append(OzonSource())" in source


def test_dispatcher_skips_ozon_when_disabled() -> None:
    source = _source("apply_dispatcher.py")

    assert 'os.getenv("OZON_ENABLED", "false")' in source
    assert "if OZON_ENABLED:" in source
    assert "first-party dispatch disabled" in source


def test_telegram_external_pool_excludes_disabled_ozon() -> None:
    source = _source("telegram_bot_pending_patch.py")

    assert 'os.getenv("OZON_ENABLED", "false")' in source
    assert 'bot_module.Vacancy.source != "ozon"' in source
