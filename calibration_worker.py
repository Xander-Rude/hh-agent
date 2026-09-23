from __future__ import annotations

import json

from app.calibration import run_calibration


def main() -> int:
    run = run_calibration()
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
