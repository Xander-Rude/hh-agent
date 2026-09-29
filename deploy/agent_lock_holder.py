from __future__ import annotations

import argparse
import sys
import time
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from background_common import AgentLock, HHProfileLock
from hh_accounts import all_accounts


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
            with ExitStack() as stack:
                # Reserve the singleton pipeline/deploy lock first, then every
                # configured HH browser profile.  APPLY no longer holds the
                # global lock, so deployment must explicitly wait for active
                # OLD/CLEAN browser work before changing production files.
                stack.enter_context(AgentLock())
                for account in all_accounts():
                    stack.enter_context(HHProfileLock(account.key))

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
