from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_ALLOY_CONFIG = Path(
    os.environ.get(
        "HH_ALLOY_CONFIG",
        r"C:\Program Files\GrafanaLabs\Alloy\config.alloy",
    )
)
DEFAULT_STATE_DIR = Path(
    os.environ.get(
        "HH_GRAFANA_DRIVE_STATE_DIR",
        r"C:\ProgramData\HHAgentGrafanaBridge",
    )
)
DEFAULT_RCLONE = Path(
    os.environ.get(
        "HH_GRAFANA_DRIVE_RCLONE",
        str(DEFAULT_STATE_DIR / "rclone.exe"),
    )
)
DEFAULT_REMOTE = os.environ.get(
    "HH_GRAFANA_DRIVE_REMOTE",
    "hh-agent-drive:HH-Agent/observability",
)
DEFAULT_SELECTOR = os.environ.get(
    "HH_GRAFANA_LOKI_SELECTOR",
    '{service="hh-agent",stream_version="v2"}',
)
DEFAULT_HOURS = int(os.environ.get("HH_GRAFANA_DRIVE_HOURS", "24"))
LOKI_LIMIT = int(os.environ.get("HH_GRAFANA_LOKI_LIMIT", "5000"))
INITIAL_CHUNK_SECONDS = int(os.environ.get("HH_GRAFANA_LOKI_CHUNK_SECONDS", "3600"))
MIN_CHUNK_NS = 1_000_000_000
HTTP_TIMEOUT = int(os.environ.get("HH_GRAFANA_LOKI_TIMEOUT", "60"))

ATTENTION_RE = re.compile(
    r"(?i)(ERROR|Traceback|Exception|HTTPStatusError|ConnectError|"
    r"failed with code=|PIPELINE: failed|manual_required|\[DEFER\]|\bWARN(?:ING)?\b)"
)


@dataclass(frozen=True)
class LokiCredentials:
    query_url: str
    username: str
    password: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc_from_ns(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{nanos:09d}Z"


def ns_from_datetime(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def extract_braced_block(text: str, start: int) -> str:
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError("Block opening brace was not found")
    depth = 0
    in_string = False
    escaped = False
    for idx in range(brace, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    raise ValueError("Unterminated Alloy block")


def resolve_alloy_assignment(block: str, name: str) -> str:
    literal = re.search(rf"\b{re.escape(name)}\s*=\s*\"([^\"]+)\"", block)
    if literal:
        return literal.group(1)

    env_ref = re.search(
        rf"\b{re.escape(name)}\s*=\s*sys\.env\(\s*\"([^\"]+)\"\s*\)",
        block,
    )
    if env_ref:
        env_name = env_ref.group(1)
        value = os.environ.get(env_name)
        if not value:
            raise RuntimeError(f"Environment variable {env_name!r} is not set")
        return value

    raise RuntimeError(f"Could not resolve {name!r} from Alloy Loki config")


def load_loki_credentials(config_path: Path = DEFAULT_ALLOY_CONFIG) -> LokiCredentials:
    text = config_path.read_text(encoding="utf-8-sig")

    candidates: list[str] = []
    for match in re.finditer(r'loki\.write\s+"[^"]+"\s*\{', text):
        block = extract_braced_block(text, match.start())
        if "/loki/api/v1/push" in block:
            candidates.append(block)

    if not candidates:
        raise RuntimeError(
            f"No loki.write block with /loki/api/v1/push found in {config_path}"
        )

    preferred = next(
        (block for block in candidates if 'loki.write "grafana_cloud"' in block),
        candidates[0],
    )

    push_url = resolve_alloy_assignment(preferred, "url")
    username = resolve_alloy_assignment(preferred, "username")
    password = resolve_alloy_assignment(preferred, "password")

    query_url = re.sub(
        r"/loki/api/v1/push/?$",
        "/loki/api/v1/query_range",
        push_url,
    )
    if query_url == push_url:
        raise RuntimeError(f"Unexpected Loki push URL: {push_url}")

    return LokiCredentials(
        query_url=query_url,
        username=username,
        password=password,
    )


def http_json(
    url: str,
    username: str,
    password: str,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "hh-agent-grafana-drive-bridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Loki HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Loki request failed: {exc}") from exc

    parsed = json.loads(payload)
    if parsed.get("status") != "success":
        raise RuntimeError(f"Loki returned non-success payload: {parsed}")
    return parsed


def loki_query_range(
    credentials: LokiCredentials,
    selector: str,
    start_ns: int,
    end_ns: int,
    limit: int = LOKI_LIMIT,
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "query": selector,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "forward",
        }
    )
    payload = http_json(
        f"{credentials.query_url}?{params}",
        credentials.username,
        credentials.password,
    )
    result = payload.get("data", {}).get("result", [])
    records: list[dict[str, Any]] = []
    for stream in result:
        labels = dict(stream.get("stream", {}))
        filename = labels.get("filename", "")
        file_name = Path(filename.replace("\\", "/")).name if filename else ""
        for raw_ts, line in stream.get("values", []):
            try:
                ts_ns = int(raw_ts)
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "ts_ns": ts_ns,
                    "ts": iso_utc_from_ns(ts_ns),
                    "file": file_name,
                    "path": filename,
                    "line": line,
                }
            )
    records.sort(key=lambda item: (item["ts_ns"], item["file"], item["line"]))
    return records


def query_complete_window(
    credentials: LokiCredentials,
    selector: str,
    start_ns: int,
    end_ns: int,
    limit: int = LOKI_LIMIT,
) -> tuple[list[dict[str, Any]], int]:
    records = loki_query_range(
        credentials,
        selector=selector,
        start_ns=start_ns,
        end_ns=end_ns,
        limit=limit,
    )
    if len(records) < limit:
        return records, 0

    if end_ns - start_ns <= MIN_CHUNK_NS:
        return records, 1

    midpoint = start_ns + ((end_ns - start_ns) // 2)
    left, left_truncated = query_complete_window(
        credentials,
        selector,
        start_ns,
        midpoint - 1,
        limit,
    )
    right, right_truncated = query_complete_window(
        credentials,
        selector,
        midpoint,
        end_ns,
        limit,
    )
    return left + right, left_truncated + right_truncated


def collect_window(
    credentials: LokiCredentials,
    selector: str,
    start_ns: int,
    end_ns: int,
) -> tuple[list[dict[str, Any]], int]:
    all_records: list[dict[str, Any]] = []
    truncated_windows = 0
    chunk_ns = INITIAL_CHUNK_SECONDS * 1_000_000_000
    cursor = start_ns

    while cursor <= end_ns:
        chunk_end = min(end_ns, cursor + chunk_ns - 1)
        records, truncated = query_complete_window(
            credentials,
            selector,
            cursor,
            chunk_end,
        )
        all_records.extend(records)
        truncated_windows += truncated
        cursor = chunk_end + 1

    seen: set[tuple[int, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for record in sorted(
        all_records,
        key=lambda item: (item["ts_ns"], item["file"], item["line"]),
    ):
        key = (record["ts_ns"], record["path"], record["line"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped, truncated_windows


def is_attention(record: dict[str, Any]) -> bool:
    return bool(ATTENTION_RE.search(record.get("line", "")))


def count_lines(
    records: list[dict[str, Any]],
    *,
    file_name: str | None = None,
    contains: str | None = None,
    regex: re.Pattern[str] | None = None,
) -> int:
    count = 0
    for record in records:
        if file_name and record.get("file") != file_name:
            continue
        line = record.get("line", "")
        if contains is not None and contains not in line:
            continue
        if regex is not None and not regex.search(line):
            continue
        count += 1
    return count


def count_unique_application_ids(
    records: list[dict[str, Any]],
    *,
    contains: str,
) -> int:
    application_ids: set[str] = set()
    for record in records:
        line = record.get("line", "")
        if contains not in line:
            continue
        match = re.search(r"application_id[=:](\d+)", line, re.I)
        if match:
            application_ids.add(match.group(1))
    return len(application_ids)


def build_summary(
    records: list[dict[str, Any]],
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
    selector: str,
    truncated_windows: int,
) -> dict[str, Any]:
    files = Counter(record.get("file") or "(unknown)" for record in records)
    attention = [record for record in records if is_attention(record)]
    pipeline_regex = re.compile(r"failed with code=|PIPELINE: failed", re.I)

    pipeline_events = [
        record
        for record in records
        if record.get("file") == "pipeline_supervisor.log"
        and (
            "PIPELINE START" in record.get("line", "")
            or "PIPELINE DONE" in record.get("line", "")
            or pipeline_regex.search(record.get("line", ""))
        )
    ]

    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "hours": round((window_end - window_start).total_seconds() / 3600, 3),
        },
        "selector": selector,
        "total_lines": len(records),
        "truncated_windows": truncated_windows,
        "active_files": len(files),
        "lines_by_file": dict(sorted(files.items())),
        "counters": {
            "pipeline_completed": count_lines(
                records,
                file_name="pipeline_supervisor.log",
                contains="PIPELINE DONE",
            ),
            "pipeline_failed": count_lines(
                records,
                file_name="pipeline_supervisor.log",
                regex=pipeline_regex,
            ),
            "hh_vacancies_saved": count_lines(
                records,
                file_name="collector.log",
                contains="[SAVE]",
            ),
            "llm_scored": count_lines(
                records,
                file_name="processor.log",
                contains="SCORE:",
            ),
            "apply_decisions": count_lines(
                records,
                file_name="processor.log",
                contains="| APPLY",
            ),
            "review_decisions": count_lines(
                records,
                file_name="processor.log",
                contains="| REVIEW",
            ),
            "reject_decisions": count_lines(
                records,
                file_name="processor.log",
                contains="| REJECT",
            ),
            "gpu_defers": count_lines(
                records,
                file_name="processor.log",
                contains="[DEFER] GPU utilization",
            ),
            "manual_required": count_unique_application_ids(
                records,
                contains="manual_required",
            ),
            "manual_required_log_lines": count_lines(
                records,
                contains="manual_required",
            ),
            "attention_lines": len(attention),
        },
        "latest_pipeline_events": [
            {
                "ts": record["ts"],
                "line": record["line"],
            }
            for record in pipeline_events[-20:]
        ],
        "latest_attention": [
            {
                "ts": record["ts"],
                "file": record["file"],
                "line": record["line"],
            }
            for record in attention[-100:]
        ],
    }


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    ) as handle:
        handle.write(content)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def write_outputs(
    state_dir: Path,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Path]:
    all_path = state_dir / "hh-agent-last-24h.jsonl"
    errors_path = state_dir / "hh-agent-errors-24h.jsonl"
    summary_path = state_dir / "hh-agent-summary.json"

    def to_jsonl(items: list[dict[str, Any]]) -> str:
        lines = []
        for record in items:
            public_record = {
                "ts": record["ts"],
                "file": record["file"],
                "line": record["line"],
            }
            lines.append(json.dumps(public_record, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    atomic_write_text(all_path, to_jsonl(records))
    atomic_write_text(
        errors_path,
        to_jsonl([record for record in records if is_attention(record)]),
    )
    atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )

    return {
        "all": all_path,
        "errors": errors_path,
        "summary": summary_path,
    }


def rclone_upload(
    rclone_path: Path,
    remote: str,
    files: dict[str, Path],
) -> None:
    if not rclone_path.exists():
        raise RuntimeError(f"rclone was not found: {rclone_path}")

    remote = remote.rstrip("/")
    for path in files.values():
        target = f"{remote}/{path.name}"
        proc = subprocess.run(
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
            ],
            capture_output=True,
            text=True,
            timeout=240,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"rclone upload failed for {path.name}: {detail[:1500]}"
            )


def configure_logging(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "bridge.log"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the last HH Agent Loki window from Grafana Cloud to Google Drive."
    )
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--selector", default=DEFAULT_SELECTOR)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--alloy-config", type=Path, default=DEFAULT_ALLOY_CONFIG)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--rclone", type=Path, default=DEFAULT_RCLONE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.state_dir)

    started = time.monotonic()
    generated_at = utc_now()
    window_end = generated_at
    window_start = window_end - timedelta(hours=args.hours)

    logging.info(
        "Starting Grafana -> Drive export: selector=%s window=%sh",
        args.selector,
        args.hours,
    )

    credentials = load_loki_credentials(args.alloy_config)
    records, truncated_windows = collect_window(
        credentials,
        selector=args.selector,
        start_ns=ns_from_datetime(window_start),
        end_ns=ns_from_datetime(window_end),
    )

    summary = build_summary(
        records,
        generated_at=generated_at,
        window_start=window_start,
        window_end=window_end,
        selector=args.selector,
        truncated_windows=truncated_windows,
    )
    files = write_outputs(args.state_dir, records, summary)

    if truncated_windows:
        logging.warning(
            "Loki hit the per-query limit in %s one-second window(s); snapshot may be incomplete",
            truncated_windows,
        )

    if not args.no_upload:
        rclone_upload(args.rclone, args.remote, files)

    elapsed = time.monotonic() - started
    logging.info(
        "Export complete: lines=%s files=%s attention=%s upload=%s elapsed=%.1fs",
        len(records),
        summary["active_files"],
        summary["counters"]["attention_lines"],
        not args.no_upload,
        elapsed,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        try:
            logging.exception("Bridge failed: %s", exc)
        except Exception:
            print(f"Bridge failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
