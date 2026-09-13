from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from background_common import AgentLock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hold HH Agent's shared AgentLock for a deployment."
    )
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=2 * 60 * 60)
    parser.add_argument("--retry-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()

    args.ready.parent.mkdir(parents=True, exist_ok=True)
    args.ready.unlink(missing_ok=True)
    args.release.unlink(missing_ok=True)

    while True:
        try:
            with AgentLock():
                args.ready.write_text("ready\n", encoding="utf-8")

                while not args.release.exists():
                    time.sleep(0.5)

                return 0
        except RuntimeError as exc:
            if str(exc) != "agent_lock_busy":
                raise

            if time.monotonic() - started >= args.timeout_seconds:
                return 75

            time.sleep(args.retry_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
