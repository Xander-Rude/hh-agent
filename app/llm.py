import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from ollama import Client


load_dotenv()


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


class LLMProvider:
    def __init__(self) -> None:
        self.provider = os.getenv(
            "LLM_PROVIDER",
            "ollama",
        )

        self.model = os.getenv(
            "LLM_MODEL",
            "gemma4:12b",
        )

        self.base_url = os.getenv(
            "LLM_BASE_URL",
            "http://localhost:11434",
        )

        self.timeout = float(
            os.getenv(
                "LLM_TIMEOUT",
                "180",
            )
        )

        self.max_retries = int(
            os.getenv(
                "LLM_MAX_RETRIES",
                "2",
            )
        )

        self.think = _env_bool(
            "LLM_THINK",
            False,
        )

        self.num_ctx = int(
            os.getenv(
                "LLM_NUM_CTX",
                "8192",
            )
        )

        if self.provider != "ollama":
            raise ValueError(
                f"Unsupported LLM provider: {self.provider}"
            )

        self._create_client()

    def _create_client(self) -> None:
        self.client = Client(
            host=self.base_url,
            timeout=httpx.Timeout(
                timeout=self.timeout,
                connect=10.0,
            ),
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        format_schema: dict[str, Any] | None = None,
    ):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "think": self.think,
            "options": {
                "temperature": 0.2,
                "num_ctx": self.num_ctx,
            },
        }

        if format_schema is not None:
            kwargs["format"] = format_schema

        last_error = None

        for attempt in range(
            self.max_retries + 1
        ):
            try:
                return self.client.chat(
                    **kwargs
                )

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                last_error = exc

                print(
                    f"  [WARN] Ollama не ответила. "
                    f"Попытка {attempt + 1}/"
                    f"{self.max_retries + 1}"
                )

                if attempt >= self.max_retries:
                    break

                # Пересоздаём HTTP-клиент,
                # чтобы не использовать зависшее соединение.
                self._create_client()

                time.sleep(2)

        raise RuntimeError(
            f"Ollama не ответила после "
            f"{self.max_retries + 1} попыток. "
            f"Последняя ошибка: {last_error}"
        )