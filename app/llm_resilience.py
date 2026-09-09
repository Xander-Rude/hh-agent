from __future__ import annotations

import os


DEFAULT_PROCESSOR_LLM_TIMEOUT_SECONDS = 60
DEFAULT_PROCESSOR_LLM_MAX_RETRIES = 0
DEFAULT_MAX_CONSECUTIVE_LLM_DEFERS = 2


def configure_processor_llm_environment() -> tuple[float, int]:
    """Configure shorter Ollama transport retries for vacancy processing.

    VacancyEvaluator already has its own structured-response retry loop. Keeping
    Ollama transport retries at zero prevents a nested 3 x 3 retry explosion.
    The evaluator may still retry the whole request, but each transport timeout
    is bounded by PROCESSOR_LLM_TIMEOUT_SECONDS.
    """
    timeout = float(
        os.getenv(
            "PROCESSOR_LLM_TIMEOUT_SECONDS",
            str(DEFAULT_PROCESSOR_LLM_TIMEOUT_SECONDS),
        )
    )
    max_retries = int(
        os.getenv(
            "PROCESSOR_LLM_MAX_RETRIES",
            str(DEFAULT_PROCESSOR_LLM_MAX_RETRIES),
        )
    )

    timeout = max(5.0, timeout)
    max_retries = max(0, max_retries)

    os.environ["LLM_TIMEOUT"] = str(timeout)
    os.environ["LLM_MAX_RETRIES"] = str(max_retries)

    return timeout, max_retries


def max_consecutive_llm_defers() -> int:
    value = int(
        os.getenv(
            "PROCESSOR_MAX_CONSECUTIVE_LLM_DEFERS",
            str(DEFAULT_MAX_CONSECUTIVE_LLM_DEFERS),
        )
    )
    return max(1, value)


def _exception_messages(exc: BaseException) -> list[str]:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__

    return messages


def is_transient_ollama_failure(exc: BaseException) -> bool:
    """Return True only for transport/unavailability style Ollama failures."""
    text = "\n".join(_exception_messages(exc))

    markers = (
        "ollama не ответила",
        "timed out",
        "timeout",
        "connecterror",
        "connection refused",
        "connection reset",
        "networkerror",
        "server disconnected",
    )

    return any(marker in text for marker in markers)
