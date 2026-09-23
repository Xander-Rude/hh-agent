from __future__ import annotations

import json
from pathlib import Path

from app.calibration import run_calibration
from background_common import ROOT


REPORT_DIR = ROOT / "data" / "calibration"


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _write_report(run) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_id": run.id,
        "status": run.status,
        "scope": run.scope,
        "sample_count": run.sample_count,
        "mature_sample_count": run.mature_sample_count,
        "new_mature_sample_count": run.new_mature_sample_count,
        "event_high_watermark": run.event_high_watermark,
        "snapshot_high_watermark": run.snapshot_high_watermark,
        "dataset_hash": run.dataset_hash,
        "prompt_version": run.prompt_version,
        "llm_model": run.llm_model,
        "metrics": _loads(run.metrics_json, {}),
        "report": _loads(run.llm_report_json, None),
        "error": run.error,
        "created_at": (
            run.created_at.isoformat()
            if run.created_at is not None
            else None
        ),
    }

    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    run_path = REPORT_DIR / f"run-{run.id}.json"
    latest_path = REPORT_DIR / "latest.json"
    run_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return latest_path


def main() -> int:
    run = run_calibration()
    report_path = _write_report(run)

    payload = {
        "run_id": run.id,
        "status": run.status,
        "scope": run.scope,
        "sample_count": run.sample_count,
        "mature_sample_count": run.mature_sample_count,
        "new_mature_sample_count": run.new_mature_sample_count,
        "event_high_watermark": run.event_high_watermark,
        "snapshot_high_watermark": run.snapshot_high_watermark,
        "dataset_hash": run.dataset_hash,
        "prompt_version": run.prompt_version,
        "llm_model": run.llm_model,
        "report_path": str(report_path),
        "error": run.error,
    }
    print(
        "[CALIBRATION] "
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if run.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
