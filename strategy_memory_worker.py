from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.db import init_db
from app.strategy_memory import (
    activate_memory_version,
    create_memory_version,
    get_active_memory,
    list_memory_versions,
    rollback_memory_version,
)


ROOT = Path(__file__).resolve().parent


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_seed(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("strategy memory seed must be a JSON object")
    return payload


def _resolved_source_hash(
    seed: dict,
) -> tuple[str | None, str | None]:
    source_ref = seed.get("candidate_profile_source_ref")
    source_hash = seed.get("candidate_profile_source_hash")

    if source_ref is not None:
        source_ref = str(source_ref).strip() or None
    if source_hash is not None:
        source_hash = str(source_hash).strip() or None

    if source_ref and not source_hash:
        source_path = Path(source_ref)
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        if source_path.exists() and source_path.is_file():
            source_hash = _sha256_file(source_path)

    return source_ref, source_hash


def _seed(path: Path, *, activate: bool) -> dict:
    seed = _load_seed(path)
    source_ref, source_hash = _resolved_source_hash(seed)

    version = create_memory_version(
        candidate_profile=seed.get("candidate_profile") or {},
        target_strategy=seed.get("target_strategy") or {},
        learned_patterns=seed.get("learned_patterns") or [],
        good_examples=seed.get("good_examples") or [],
        bad_examples=seed.get("bad_examples") or [],
        candidate_profile_source_ref=source_ref,
        candidate_profile_source_hash=source_hash,
        parent_version_id=seed.get("parent_version_id"),
        source=seed.get("source") or "manual_seed",
        calibration_run_id=seed.get("calibration_run_id"),
        note=seed.get("note"),
        activate=activate,
    )
    return {
        "id": version.id,
        "version_number": version.version_number,
        "content_hash": version.content_hash,
        "active": bool(
            (get_active_memory() or {}).get("version", {}).get("id")
            == version.id
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage versioned hh-agent strategy memory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed")
    seed.add_argument("--file", required=True)
    seed.add_argument(
        "--activate",
        action="store_true",
        help="Make the seeded version active.",
    )

    sub.add_parser("show-active")
    sub.add_parser("list")

    activate = sub.add_parser("activate")
    activate.add_argument("version_id", type=int)
    activate.add_argument("--note")

    rollback = sub.add_parser("rollback")
    rollback.add_argument("version_id", type=int)
    rollback.add_argument("--note")

    args = parser.parse_args()
    init_db()

    if args.command == "seed":
        result = _seed(
            Path(args.file),
            activate=bool(args.activate),
        )
    elif args.command == "show-active":
        result = get_active_memory()
    elif args.command == "list":
        result = list_memory_versions()
    elif args.command == "activate":
        result = {
            "changed": activate_memory_version(
                args.version_id,
                note=args.note,
            ),
            "active": get_active_memory(),
        }
    elif args.command == "rollback":
        result = {
            "changed": rollback_memory_version(
                args.version_id,
                note=args.note,
            ),
            "active": get_active_memory(),
        }
    else:
        raise RuntimeError(f"unsupported command: {args.command}")

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
