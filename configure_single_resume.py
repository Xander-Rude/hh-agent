from __future__ import annotations

import argparse
from pathlib import Path
import shutil

import yaml


CONFIG_PATH = Path("data/resumes.yaml")
REQUIRED_KEYS = (
    "project",
    "delivery",
    "technical_project",
    "product",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Point all logical HH Agent resume slots to one physical resume. "
            "This keeps the current four-key matcher compatible while HH.ru "
            "has only one active resume."
        )
    )
    parser.add_argument("--resume-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--source-key",
        default="delivery",
        choices=REQUIRED_KEYS,
        help="Existing resume entry whose positioning metadata will be reused.",
    )
    parser.add_argument(
        "--file-path",
        default=None,
        help=(
            "Optional local PDF path. If supplied, every logical resume key "
            "will use this same file for Yandex/VK uploads."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Не найден {CONFIG_PATH}")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    resumes = config.get("resumes")

    if not isinstance(resumes, dict) or not resumes:
        raise ValueError("В data/resumes.yaml нет непустого блока resumes")

    source = resumes.get(args.source_key)
    if not isinstance(source, dict):
        raise ValueError(
            f"В data/resumes.yaml нет исходного resume key {args.source_key!r}"
        )

    backup_path = CONFIG_PATH.with_suffix(".yaml.bak-single-resume")
    shutil.copy2(CONFIG_PATH, backup_path)

    shared = dict(source)
    shared["title"] = args.title
    shared["hh_resume_id"] = args.resume_id

    if args.file_path:
        shared["file_path"] = args.file_path

    config["resumes"] = {
        key: dict(shared)
        for key in REQUIRED_KEYS
    }
    config["generated_resumes"] = []

    strategy = dict(config.get("strategy") or {})
    strategy["fallback_resume"] = args.source_key
    config["strategy"] = strategy

    CONFIG_PATH.write_text(
        yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    print("Single-resume configuration enabled.")
    print(f"HH resume id: {args.resume_id}")
    print(f"Title: {args.title}")
    print(f"Backup: {backup_path}")
    if args.file_path:
        print(f"Local PDF: {args.file_path}")
    else:
        print(
            "Local PDF path was not changed; HH apply is safe, but Yandex/VK "
            "will keep using the existing configured file_path."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
