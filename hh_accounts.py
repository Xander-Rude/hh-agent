from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state" / "hh_accounts"


@dataclass(frozen=True)
class HHAccount:
    key: str
    label: str
    profile_dir: Path

    @property
    def state_path(self) -> Path:
        return STATE_DIR / f"{self.key}.json"


_ACCOUNTS = {
    "old": HHAccount(
        key="old",
        label="⚪ OLD",
        profile_dir=ROOT / "browser-profile",
    ),
    "clean": HHAccount(
        key="clean",
        label="🟢 CLEAN",
        profile_dir=ROOT / "browser-profile-clean",
    ),
}


def all_accounts() -> tuple[HHAccount, ...]:
    return tuple(_ACCOUNTS.values())


def get_account(key: str | None) -> HHAccount:
    normalized = (key or "").strip().lower()
    if normalized not in _ACCOUNTS:
        raise ValueError(
            f"Unknown HH account {key!r}. Expected one of: "
            + ", ".join(sorted(_ACCOUNTS))
        )
    return _ACCOUNTS[normalized]


def read_account_state(account: HHAccount | str) -> dict:
    item = get_account(account) if isinstance(account, str) else account
    try:
        return json.loads(item.state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_account_state(
    account: HHAccount | str,
    *,
    authenticated: bool,
    resume_ids: list[str] | None = None,
) -> None:
    item = get_account(account) if isinstance(account, str) else account
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = read_account_state(item)
    payload.update(
        {
            "account_key": item.key,
            "authenticated": bool(authenticated),
        }
    )
    if resume_ids is not None:
        payload["resume_ids"] = list(dict.fromkeys(resume_ids))
    item.state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def account_resume_id(account: HHAccount | str) -> str | None:
    item = get_account(account) if isinstance(account, str) else account
    env_name = f"HH_{item.key.upper()}_RESUME_ID"
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured

    state = read_account_state(item)
    resume_ids = state.get("resume_ids") or []
    if len(resume_ids) == 1:
        return str(resume_ids[0])

    # Backward compatibility with the original single-account deployment.
    if item.key == "old":
        legacy = os.getenv("HH_ACTIVE_RESUME_ID", "").strip()
        if legacy:
            return legacy
    return None


def has_saved_auth(account: HHAccount | str) -> bool:
    item = get_account(account) if isinstance(account, str) else account
    state = read_account_state(item)
    return bool(state.get("authenticated")) and item.profile_dir.exists()


def active_apply_account() -> HHAccount:
    explicit = os.getenv("HH_ACTIVE_ACCOUNT", "").strip().lower()
    if explicit:
        return get_account(explicit)

    # Safe migration path: keep using OLD until CLEAN has successfully logged in
    # at least once. After hh_login.py --account clean succeeds, CLEAN becomes
    # the default account for all new applies automatically.
    clean = get_account("clean")
    if has_saved_auth(clean):
        return clean

    return get_account("old")


def account_mode(account: HHAccount | str) -> str:
    item = get_account(account) if isinstance(account, str) else account
    return "apply" if item.key == active_apply_account().key else "observe"


def account_label(account_key: str | None) -> str:
    try:
        return get_account(account_key or active_apply_account().key).label
    except ValueError:
        return f"⚪ {(account_key or 'UNKNOWN').upper()}"


def observable_accounts() -> tuple[HHAccount, ...]:
    # OLD remains readable for historical response tracking. CLEAN is included
    # as soon as it has a saved session (or is explicitly selected).
    result: list[HHAccount] = []
    active_key = active_apply_account().key
    for item in all_accounts():
        if item.key == "old" or item.key == active_key or has_saved_auth(item):
            result.append(item)
    return tuple(result)
