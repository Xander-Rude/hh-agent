from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUStatus:
    index: int
    utilization_percent: int
    memory_used_mb: int
    memory_total_mb: int

    @property
    def memory_free_mb(self) -> int:
        return max(0, self.memory_total_mb - self.memory_used_mb)


@dataclass(frozen=True)
class GPUGuardDecision:
    defer: bool
    status: GPUStatus | None = None
    reason: str = ""
    warning: str = ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def parse_nvidia_smi_output(
    output: str,
    gpu_index: int = 0,
) -> GPUStatus:
    """Parse nvidia-smi CSV output and return the selected GPU."""
    rows: list[GPUStatus] = []

    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue

        try:
            rows.append(
                GPUStatus(
                    index=int(parts[0]),
                    utilization_percent=int(float(parts[1])),
                    memory_used_mb=int(float(parts[2])),
                    memory_total_mb=int(float(parts[3])),
                )
            )
        except ValueError:
            continue

    for row in rows:
        if row.index == gpu_index:
            return row

    if rows:
        raise ValueError(
            f"GPU index {gpu_index} not found in nvidia-smi output"
        )

    raise ValueError("nvidia-smi returned no parseable GPU rows")


def probe_gpu(gpu_index: int = 0) -> tuple[GPUStatus | None, str]:
    """Read current NVIDIA GPU utilization without opening a console window."""
    command = [
        "nvidia-smi",
        "--query-gpu=index,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]

    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "check": False,
    }

    if os.name == "nt":
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            kwargs["creationflags"] = create_no_window

    try:
        completed = subprocess.run(command, **kwargs)
    except FileNotFoundError:
        return None, "nvidia-smi не найден; GPU guard пропущен"
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi не ответил за 5 секунд; GPU guard пропущен"
    except OSError as exc:
        return None, f"nvidia-smi недоступен: {exc}"

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        return None, f"nvidia-smi завершился с code={completed.returncode}{detail}"

    try:
        return parse_nvidia_smi_output(
            completed.stdout,
            gpu_index=gpu_index,
        ), ""
    except ValueError as exc:
        return None, str(exc)


def is_gpu_busy(
    status: GPUStatus,
    *,
    utilization_threshold: int = 85,
    min_free_vram_mb: int = 3000,
    vram_utilization_floor: int = 30,
) -> tuple[bool, str]:
    """
    Decide whether Ollama should yield the GPU.

    Low free VRAM alone is not enough to defer: Ollama itself may keep a model
    resident in VRAM while idle. The VRAM rule is therefore combined with a
    modest utilization floor. Sustained high GPU utilization always defers.
    """
    if status.utilization_percent >= utilization_threshold:
        return (
            True,
            f"GPU utilization {status.utilization_percent}% >= "
            f"{utilization_threshold}%",
        )

    if (
        min_free_vram_mb > 0
        and status.memory_free_mb < min_free_vram_mb
        and status.utilization_percent >= vram_utilization_floor
    ):
        return (
            True,
            f"free VRAM {status.memory_free_mb} MB < {min_free_vram_mb} MB "
            f"при GPU utilization {status.utilization_percent}%",
        )

    return False, ""


def should_defer_ollama() -> GPUGuardDecision:
    """Return defer=True when the local NVIDIA GPU is busy enough to yield."""
    if os.getenv("LLM_PROVIDER", "ollama").strip().lower() != "ollama":
        return GPUGuardDecision(defer=False)

    if not _env_bool("GPU_GUARD_ENABLED", True):
        return GPUGuardDecision(defer=False)

    gpu_index = _env_int("GPU_GUARD_INDEX", 0)
    utilization_threshold = _env_int("GPU_GUARD_UTIL_THRESHOLD", 85)
    min_free_vram_mb = _env_int("GPU_GUARD_MIN_FREE_VRAM_MB", 3000)
    vram_utilization_floor = _env_int("GPU_GUARD_VRAM_UTIL_FLOOR", 30)
    confirm_delay = max(
        0.0,
        _env_float("GPU_GUARD_CONFIRM_DELAY_SECONDS", 1.0),
    )

    status, warning = probe_gpu(gpu_index=gpu_index)
    if status is None:
        # Monitoring failure must never make the pipeline fail closed.
        return GPUGuardDecision(
            defer=False,
            warning=warning,
        )

    busy, reason = is_gpu_busy(
        status,
        utilization_threshold=utilization_threshold,
        min_free_vram_mb=min_free_vram_mb,
        vram_utilization_floor=vram_utilization_floor,
    )
    if not busy:
        return GPUGuardDecision(
            defer=False,
            status=status,
        )

    # Confirm once to avoid deferring because of a very short utilization spike
    # caused by the previous Ollama request or desktop rendering.
    if confirm_delay:
        time.sleep(confirm_delay)

    confirmed_status, confirm_warning = probe_gpu(gpu_index=gpu_index)
    if confirmed_status is None:
        return GPUGuardDecision(
            defer=True,
            status=status,
            reason=reason,
            warning=confirm_warning,
        )

    confirmed_busy, confirmed_reason = is_gpu_busy(
        confirmed_status,
        utilization_threshold=utilization_threshold,
        min_free_vram_mb=min_free_vram_mb,
        vram_utilization_floor=vram_utilization_floor,
    )

    if not confirmed_busy:
        return GPUGuardDecision(
            defer=False,
            status=confirmed_status,
        )

    return GPUGuardDecision(
        defer=True,
        status=confirmed_status,
        reason=confirmed_reason,
    )
