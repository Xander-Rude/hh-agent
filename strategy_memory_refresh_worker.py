from __future__ import annotations

import argparse
import json

from app.db import init_db
from app.strategy_memory_refresh import propose_memory_refresh


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Propose an inactive strategy-memory version from a mature "
            "completed calibration run."
        ),
    )
    parser.add_argument(
        "--calibration-run-id",
        type=int,
        default=None,
        help="Specific completed calibration run. Defaults to latest completed.",
    )
    args = parser.parse_args()

    init_db()
    result = propose_memory_refresh(
        calibration_run_id=args.calibration_run_id,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "calibration_run_id": result.calibration_run_id,
                "memory_version_id": result.memory_version_id,
                "memory_version_number": result.memory_version_number,
                "accepted_pattern_count": result.accepted_pattern_count,
                "rejected_pattern_count": result.rejected_pattern_count,
                "activated": result.activated,
                "reason": result.reason,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
