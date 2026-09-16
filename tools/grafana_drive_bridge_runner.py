from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# pythonw.exe has no attached console. Keep logging safe by giving the bridge
# writable sinks even when stdout/stderr are absent.
_DEVNULL_HANDLES = []
for stream_name in ("stdout", "stderr"):
    if getattr(sys, stream_name) is None:
        handle = open(os.devnull, "w", encoding="utf-8")
        _DEVNULL_HANDLES.append(handle)
        setattr(sys, stream_name, handle)

import grafana_drive_bridge as bridge  # noqa: E402


def parse_runner_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remote", default=bridge.DEFAULT_REMOTE)
    parser.add_argument("--state-dir", type=Path, default=bridge.DEFAULT_STATE_DIR)
    parser.add_argument("--rclone", type=Path, default=bridge.DEFAULT_RCLONE)
    args, _ = parser.parse_known_args()
    return args


def subprocess_window_kwargs() -> dict[str, int]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_rclone(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=240,
        **subprocess_window_kwargs(),
    )


def hidden_rclone_upload(
    rclone_path: Path,
    remote: str,
    files: dict[str, Path],
) -> None:
    if not rclone_path.exists():
        raise RuntimeError(f"rclone was not found: {rclone_path}")

    remote = remote.rstrip("/")
    for path in files.values():
        target = f"{remote}/{path.name}"
        proc = run_rclone(
            [
                str(rclone_path),
                "copyto",
                str(path),
                target,
                "--retries",
                "3",
                "--low-level-retries",
                "5",
                "--timeout",
                "60s",
            ]
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"rclone upload failed for {path.name}: {detail[:1500]}"
            )


def safe_log_name(value: object) -> str | None:
    if not value:
        return None
    name = Path(str(value).replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return None
    return name


def write_per_log_files(state_dir: Path) -> Path:
    source = state_dir / "hh-agent-last-24h.jsonl"
    if not source.exists():
        raise RuntimeError(f"Combined snapshot was not found: {source}")

    grouped: dict[str, list[str]] = defaultdict(list)
    with source.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            record = json.loads(raw_line)
            name = safe_log_name(record.get("file"))
            if not name:
                continue
            grouped[name].append(str(record.get("line", "")))

    logs_dir = state_dir / "logs"
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    for name, lines in sorted(grouped.items()):
        path = logs_dir / name
        text = "\n".join(lines)
        if text:
            text += "\n"
        path.write_text(text, encoding="utf-8", newline="\n")

    logging.info("Prepared per-log snapshots: files=%s dir=%s", len(grouped), logs_dir)
    return logs_dir


def sync_per_log_files(rclone_path: Path, remote: str, logs_dir: Path) -> None:
    if not rclone_path.exists():
        raise RuntimeError(f"rclone was not found: {rclone_path}")

    target = f"{remote.rstrip('/')}/logs"
    proc = run_rclone(
        [
            str(rclone_path),
            "sync",
            str(logs_dir),
            target,
            "--retries",
            "3",
            "--low-level-retries",
            "5",
            "--timeout",
            "60s",
        ]
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"rclone per-log sync failed: {detail[:1500]}")

    logging.info("Per-log snapshots synced to %s", target)


def main() -> int:
    args = parse_runner_args()

    try:
        # The scheduled task itself is windowless via pythonw.exe, but rclone.exe
        # is a console application. Override the bridge uploader so every rclone
        # child process is created with CREATE_NO_WINDOW on Windows as well.
        bridge.rclone_upload = hidden_rclone_upload

        result = bridge.main()
        if result != 0:
            return int(result)

        logs_dir = write_per_log_files(args.state_dir)
        sync_per_log_files(args.rclone, args.remote, logs_dir)
        return 0
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        try:
            logging.exception("Bridge runner failed: %s", exc)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
