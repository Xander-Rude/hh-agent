import os
import unittest
from unittest.mock import patch

from app.llm_resilience import (
    configure_processor_llm_environment,
    is_transient_ollama_failure,
    max_consecutive_llm_defers,
)


class LlmResilienceTests(unittest.TestCase):
    def test_processor_defaults_remove_nested_transport_retries(self):
        with patch.dict(os.environ, {}, clear=True):
            timeout, retries = configure_processor_llm_environment()

            self.assertEqual(timeout, 60.0)
            self.assertEqual(retries, 0)
            self.assertEqual(os.environ["LLM_TIMEOUT"], "60.0")
            self.assertEqual(os.environ["LLM_MAX_RETRIES"], "0")

    def test_processor_values_are_configurable(self):
        with patch.dict(
            os.environ,
            {
                "PROCESSOR_LLM_TIMEOUT_SECONDS": "90",
                "PROCESSOR_LLM_MAX_RETRIES": "1",
                "PROCESSOR_MAX_CONSECUTIVE_LLM_DEFERS": "3",
            },
            clear=True,
        ):
            timeout, retries = configure_processor_llm_environment()

            self.assertEqual(timeout, 90.0)
            self.assertEqual(retries, 1)
            self.assertEqual(max_consecutive_llm_defers(), 3)

    def test_timeout_wrapped_by_evaluator_is_transient(self):
        try:
            try:
                raise RuntimeError(
                    "Ollama не ответила после 1 попыток. Последняя ошибка: timed out"
                )
            except RuntimeError as inner:
                raise RuntimeError(
                    "VacancyEvaluator не получил корректный structured response"
                ) from inner
        except RuntimeError as outer:
            self.assertTrue(is_transient_ollama_failure(outer))

    def test_structured_response_error_is_not_transient(self):
        exc = RuntimeError(
            "VacancyEvaluator не получил корректный structured response после 3 попыток"
        )
        self.assertFalse(is_transient_ollama_failure(exc))

    def test_minimums_prevent_pathological_configuration(self):
        with patch.dict(
            os.environ,
            {
                "PROCESSOR_LLM_TIMEOUT_SECONDS": "1",
                "PROCESSOR_LLM_MAX_RETRIES": "-10",
                "PROCESSOR_MAX_CONSECUTIVE_LLM_DEFERS": "0",
            },
            clear=True,
        ):
            timeout, retries = configure_processor_llm_environment()

            self.assertEqual(timeout, 5.0)
            self.assertEqual(retries, 0)
            self.assertEqual(max_consecutive_llm_defers(), 1)


if __name__ == "__main__":
    unittest.main()
