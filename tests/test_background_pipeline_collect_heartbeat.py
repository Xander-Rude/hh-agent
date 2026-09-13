import time

import background_pipeline as pipeline


def test_hh_collect_has_no_wall_clock_timeout_and_pulses_state(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(
        pipeline,
        "set_stage",
        lambda stage, **kwargs: calls.append(stage),
    )

    def fake_run_python(script_name, **kwargs):
        assert script_name == "hh_collect_optimized.py"
        assert kwargs["timeout_seconds"] is None
        time.sleep(0.04)
        return 0

    monkeypatch.setattr(pipeline, "run_python", fake_run_python)

    assert pipeline._run_hh_collect() == 0
    assert "collect_hh" in calls
