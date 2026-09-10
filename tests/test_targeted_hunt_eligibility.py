import json
from types import SimpleNamespace

from app.targeted_hunt.eligibility import evaluate_eligibility


def _vacancy(company="Acme"):
    return SimpleNamespace(company=company)


def _evaluation(**overrides):
    values = dict(
        decision="apply",
        score=90,
        role_match=90,
        seniority_match=90,
        red_flags=json.dumps([]),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_strong_clean_match_is_eligible(monkeypatch):
    monkeypatch.delenv("TARGETED_HUNT_MIN_SCORE", raising=False)
    result = evaluate_eligibility(_vacancy(), _evaluation())
    assert result.eligible is True
    assert result.score == 90


def test_red_flag_blocks_expensive_research(monkeypatch):
    monkeypatch.delenv("TARGETED_HUNT_MAX_RED_FLAGS", raising=False)
    result = evaluate_eligibility(
        _vacancy(),
        _evaluation(red_flags=json.dumps(["English below must-have"])),
    )
    assert result.eligible is False
    assert "red_flags" in result.reason


def test_aggregate_score_does_not_override_weak_role_match():
    result = evaluate_eligibility(_vacancy(), _evaluation(score=95, role_match=60))
    assert result.eligible is False
    assert "role" in result.reason
