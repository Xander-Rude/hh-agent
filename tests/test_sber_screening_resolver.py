from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.sber_screening import resolve_application_for_vacancy_title


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        return _Rows(self._rows)


class SberScreeningResolverTest(unittest.TestCase):
    def test_known_sber_vacancy_can_use_context_row_without_applied_at(self):
        application = SimpleNamespace(id=2739, applied_at=None)
        vacancy = SimpleNamespace(
            title="Лидер проектного офиса",
            company="Сбер для экспертов",
        )

        with patch(
            "app.sber_screening.SessionLocal",
            return_value=_Session([(application, vacancy)]),
        ):
            resolved = resolve_application_for_vacancy_title(
                "Лидер проектного офиса"
            )

        self.assertEqual(resolved, 2739)


if __name__ == "__main__":
    unittest.main()
