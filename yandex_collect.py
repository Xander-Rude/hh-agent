from __future__ import annotations

"""Совместимый wrapper для старых ручных запусков.

Основная реализация Yandex Jobs теперь живёт в sources/yandex.py,
а общий запуск карьерных источников — в collect_careers.py.
"""

from collect_careers import main


if __name__ == "__main__":
    raise SystemExit(main())
