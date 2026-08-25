from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_FILES = (
    LOG_DIR / "yandex_apply_worker.log",
    LOG_DIR / "yandex_apply_worker_attention.log",
)


def extract_blocks(text: str, application_id: int) -> list[str]:
    marker = f"Application ID: {application_id}"
    if marker not in text:
        return []

    blocks: list[str] = []
    chunks = text.split("\n[")

    for index, chunk in enumerate(chunks):
        candidate = chunk if index == 0 else "[" + chunk
        if marker in candidate:
            blocks.append(candidate.strip())

    return blocks


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python inspect_yandex_apply_log.py <application_id>")
        return 2

    try:
        application_id = int(sys.argv[1])
    except ValueError:
        print("Application ID должен быть целым числом")
        return 2

    print("=" * 80)
    print("YANDEX APPLICATION LOG DIAGNOSTIC")
    print(f"Application ID: {application_id}")
    print("=" * 80)

    found = False

    for path in LOG_FILES:
        print(f"\n--- {path} ---")
        if not path.exists():
            print("Файл отсутствует")
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = extract_blocks(text, application_id)

        if not blocks:
            print("Упоминаний Application ID не найдено")
            continue

        found = True
        for block in blocks:
            print(block)
            print("-" * 80)

    if not found:
        print(
            "\n[INFO] В Yandex worker logs нет истории этой Application. "
            "Значит status мог быть изменён другим кодом/ручным запуском; "
            "перед requeue нужно проверить другие runtime-источники."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
