import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from ollama import Client


load_dotenv()


class LLMProvider:
    def __init__(self) -> None:
        self.provider = os.getenv(
            "LLM_PROVIDER",
            "ollama",
        )

        self.model = os.getenv(
            "LLM_MODEL",
            "gemma3:12b-it-qat",
        )

        self.base_url = os.getenv(
            "LLM_BASE_URL",
            "http://localhost:11434",
        )

        self.timeout = float(
            os.getenv(
                "LLM_TIMEOUT",
                "120",
            )
        )

        self.max_retries = int(
            os.getenv(
                "LLM_MAX_RETRIES",
                "2",
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
            "options": {
                "temperature": 0.2,
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