from __future__ import annotations

import argparse
import json

from app.clean_funnel import (
    current_clean_funnel_snapshot,
    format_clean_funnel_lines,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show current CLEAN discovery-to-application funnel."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full structured snapshot as JSON.",
    )
    args = parser.parse_args()

    snapshot = current_clean_funnel_snapshot()

    if args.json:
        print(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for line in format_clean_funnel_lines(snapshot):
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
