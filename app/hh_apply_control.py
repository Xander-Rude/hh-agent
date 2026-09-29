from __future__ import annotations

import json
import os
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "runtime" / "hh_apply_control"

CAPTCHA_TEXT_MARKERS = (
    "captcha",
    "капча",
    "подтвердите, что вы не робот",
    "проверка, что вы не робот",
    "введите код с картинки",
    "verify you are human",
    "security check",
    "подозрительная активность",
)

OLD_MIN_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("HH_OLD_APPLY_MIN_DELAY_SECONDS", "180")),
)
OLD_MAX_DELAY_SECONDS = max(
    OLD_MIN_DELAY_SECONDS,
    float(os.getenv("HH_OLD_APPLY_MAX_DELAY_SECONDS", "300")),
)
OLD_MAX_PER_HOUR = max(
    1,
    int(os.getenv("HH_OLD_APPLY_MAX_PER_HOUR", "12")),
)
OLD_MAX_PER_DAY = max(
    1,
    int(os.getenv("HH_OLD_APPLY_MAX_PER_DAY", "180")),
)
OLD_MAX_WAIT_PER_RUN_SECONDS = max(
    0.0,
    float(os.getenv("HH_OLD_APPLY_MAX_WAIT_PER_RUN_SECONDS", "360")),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def pause_path(account_key: str) -> Path:
    return STATE_DIR / f"{account_key.strip().lower()}_captcha_pause.json"


def rate_path(account_key: str) -> Path:
    return STATE_DIR / f"{account_key.strip().lower()}_rate.json"


def captcha_pause(account_key: str) -> dict | None:
    payload = _read(pause_path(account_key))
    if not payload.get("paused"):
        return None
    return payload


def is_captcha_paused(account_key: str) -> bool:
    return captcha_pause(account_key) is not None


def pause_for_captcha(
    account_key: str,
    reason: str,
    *,
    application_id: int | None = None,
) -> dict:
    payload = {
        "paused": True,
        "account_key": account_key.strip().lower(),
        "paused_at": _now().isoformat(),
        "reason": str(reason or "captcha")[:2000],
        "application_id": application_id,
    }
    _atomic_write(pause_path(account_key), payload)
    return payload


def clear_captcha_pause(account_key: str) -> bool:
    path = pause_path(account_key)
    existed = path.exists()
    path.unlink(missing_ok=True)

    # OLD collector predates the shared pause and keeps a timed cooldown file.
    # Clear it only after the operator explicitly confirms the captcha is solved.
    if account_key.strip().lower() == "old":
        (ROOT / "data" / "hh_collect_cooldown.json").unlink(missing_ok=True)
    else:
        (
            ROOT / "data" / f"hh_collect_cooldown_{account_key.strip().lower()}.json"
        ).unlink(missing_ok=True)
    return existed


def captcha_reason_from_page(page) -> str | None:
    try:
        current_url = str(page.url or "").lower()
    except Exception:
        current_url = ""

    if "captcha" in current_url or "challenge" in current_url:
        return f"HH captcha/challenge URL: {current_url[:500]}"

    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        text = ""

    for marker in CAPTCHA_TEXT_MARKERS:
        if marker in text:
            return f"HH captcha marker: {marker}"
    return None


def _parse_dt(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _recent_attempts(account_key: str, now: datetime) -> list[datetime]:
    payload = _read(rate_path(account_key))
    result: list[datetime] = []
    cutoff = now - timedelta(days=1)
    for raw in payload.get("attempts", []):
        item = _parse_dt(raw)
        if item is not None and item >= cutoff:
            result.append(item)
    return sorted(result)


def seconds_until_old_slot(
    *,
    now: datetime | None = None,
) -> tuple[float, str]:
    current = (now or _now()).astimezone(UTC)
    payload = _read(rate_path("old"))
    waits: list[tuple[float, str]] = []

    next_allowed = _parse_dt(payload.get("next_allowed_at"))
    if next_allowed is not None and next_allowed > current:
        waits.append(
            ((next_allowed - current).total_seconds(), "inter-apply delay")
        )

    attempts = _recent_attempts("old", current)
    hour_cutoff = current - timedelta(hours=1)
    hour = [item for item in attempts if item > hour_cutoff]
    if len(hour) >= OLD_MAX_PER_HOUR:
        waits.append(
            (
                max(
                    0.0,
                    (hour[-OLD_MAX_PER_HOUR] + timedelta(hours=1) - current)
                    .total_seconds(),
                ),
                "hourly rate limit",
            )
        )

    if len(attempts) >= OLD_MAX_PER_DAY:
        waits.append(
            (
                max(
                    0.0,
                    (attempts[-OLD_MAX_PER_DAY] + timedelta(days=1) - current)
                    .total_seconds(),
                ),
                "daily rate limit",
            )
        )

    if not waits:
        return 0.0, "ready"
    return max(waits, key=lambda item: item[0])


def wait_for_old_slot(
    *,
    sleep=time.sleep,
    max_wait_seconds: float | None = None,
) -> tuple[bool, str]:
    if is_captcha_paused("old"):
        return False, "captcha paused"

    wait_seconds, reason = seconds_until_old_slot()
    limit = (
        OLD_MAX_WAIT_PER_RUN_SECONDS
        if max_wait_seconds is None
        else max(0.0, float(max_wait_seconds))
    )
    if wait_seconds > limit:
        return False, (
            f"{reason}; next slot in {int(wait_seconds)}s exceeds "
            f"this run wait budget {int(limit)}s"
        )
    if wait_seconds > 0:
        sleep(wait_seconds)

    if is_captcha_paused("old"):
        return False, "captcha paused"
    return True, reason


def record_old_apply_attempt(
    *,
    now: datetime | None = None,
    rng=random.uniform,
) -> dict:
    current = (now or _now()).astimezone(UTC)
    attempts = _recent_attempts("old", current)
    attempts.append(current)
    delay = rng(OLD_MIN_DELAY_SECONDS, OLD_MAX_DELAY_SECONDS)
    payload = {
        "account_key": "old",
        "attempts": [item.isoformat() for item in attempts],
        "last_attempt_at": current.isoformat(),
        "next_allowed_at": (current + timedelta(seconds=delay)).isoformat(),
        "delay_seconds": delay,
    }
    _atomic_write(rate_path("old"), payload)
    return payload
