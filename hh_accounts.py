from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state" / "hh_accounts"
CLEAN_DEFAULT_RESUME_ID = "b5d6fbf3ff1124b2890039ed1f394633454535"


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
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
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
    if authenticated and not payload.get("activated_at"):
        payload["activated_at"] = (
            datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
        )
    if resume_ids is not None:
        payload["resume_ids"] = list(dict.fromkeys(resume_ids))
    item.state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )




def account_activated_at(account: HHAccount | str) -> datetime | None:
    item = get_account(account) if isinstance(account, str) else account
    state = read_account_state(item)

    raw = str(state.get("activated_at") or "").strip()
    if raw:
        try:
            value = datetime.fromisoformat(raw)
            if value.tzinfo is not None:
                value = value.astimezone(UTC).replace(tzinfo=None)
            return value
        except ValueError:
            pass

    # Backward compatibility for accounts authenticated before activated_at
    # was introduced. The state file was written when login was persisted, so
    # its mtime is the safest cutoff available for suppressing old backlog.
    try:
        return datetime.fromtimestamp(
            item.state_path.stat().st_mtime,
            tz=UTC,
        ).replace(tzinfo=None)
    except OSError:
        return None

def account_resume_id(account: HHAccount | str) -> str | None:
    item = get_account(account) if isinstance(account, str) else account
    key = str(getattr(item, "key", "") or "").strip().lower()

    if key:
        env_name = f"HH_{key.upper()}_RESUME_ID"
        configured = os.getenv(env_name, "").strip()
        if configured:
            return configured

    state = (
        read_account_state(item)
        if hasattr(item, "state_path")
        else {}
    )
    resume_ids = state.get("resume_ids") or []
    if len(resume_ids) == 1:
        return str(resume_ids[0])

    # Backward compatibility with the original single-account deployment.
    if key == "old":
        legacy = os.getenv("HH_ACTIVE_RESUME_ID", "").strip()
        if legacy:
            return legacy

    if key == "clean":
        return CLEAN_DEFAULT_RESUME_ID

    return None


def has_saved_auth(account: HHAccount | str) -> bool:
    item = get_account(account) if isinstance(account, str) else account
    state = read_account_state(item)

    if bool(state.get("authenticated")):
        return item.profile_dir.exists()

    # OLD predates per-account state files. Preserve the legacy browser-profile
    # as an apply candidate when it exists; the live session/identity guard
    # still decides whether any HH action is allowed.
    if item.key == "old" and item.profile_dir.exists():
        return True

    return False


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
    apply_keys = {candidate.key for candidate in apply_accounts()}
    return "apply" if item.key in apply_keys else "observe"


def account_label(account_key: str | None) -> str:
    try:
        return get_account(account_key or active_apply_account().key).label
    except ValueError:
        return f"⚪ {(account_key or 'UNKNOWN').upper()}"


def apply_accounts() -> tuple[HHAccount, ...]:
    """Accounts eligible for live HH apply work.

    Keep the current active account for backward compatibility, then include
    every additional account with a saved isolated HH session.
    """

    active = active_apply_account()
    result: list[HHAccount] = [active]

    for item in all_accounts():
        if item.key == active.key:
            continue
        if has_saved_auth(item):
            result.append(item)

    return tuple(result)


def account_for_worker() -> HHAccount:
    """Resolve the account pinned to one worker process.

    A worker must never silently switch accounts after it starts. Supervisors
    set HH_WORKER_ACCOUNT explicitly; legacy single-account launches keep using
    active_apply_account().
    """

    explicit = os.getenv("HH_WORKER_ACCOUNT", "").strip().lower()
    if explicit:
        return get_account(explicit)
    return active_apply_account()


def observable_accounts() -> tuple[HHAccount, ...]:
    # Response sync may observe historical OLD applications even when OLD is
    # not currently the preferred apply account.
    result: list[HHAccount] = []
    active_key = active_apply_account().key
    for item in all_accounts():
        if item.key == "old" or item.key == active_key or has_saved_auth(item):
            result.append(item)
    return tuple(result)
