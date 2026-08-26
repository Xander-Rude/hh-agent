from __future__ import annotations

from sources import TBankSource, VKSource, YandexSource
from sources.base import save_vacancy


SOURCES = [
    YandexSource(),
    VKSource(),
    TBankSource(),
]


def main() -> int:
    print("=" * 80)
    print("CAREER SITES COLLECTOR")
    print("=" * 80)

    total_added = 0
    total_skipped = 0
    total_errors = 0
    failed_sources = 0

    for source in SOURCES:
        print()
        print(f"[SOURCE] {source.name}")

        try:
            result = source.collect()
        except Exception as exc:
            failed_sources += 1
            total_errors += 1
            print(
                f"[{source.name.upper()}] FATAL: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        added = 0
        for raw in result.vacancies:
            try:
                if save_vacancy(raw):
                    added += 1
                    print(f"[{source.name.upper()}] ADDED: {raw.title}")
                else:
                    result.skipped += 1
            except Exception as exc:
                result.errors += 1
                print(
                    f"[{source.name.upper()}] SAVE ERROR {raw.url}: "
                    f"{type(exc).__name__}: {exc}"
                )

        total_added += added
        total_skipped += result.skipped
        total_errors += result.errors

        print(
            f"[{source.name.upper()}] ИТОГО: "
            f"добавлено={added}; пропущено={result.skipped}; "
            f"ошибки={result.errors}"
        )

    print()
    print("=" * 80)
    print(
        f"Добавлено: {total_added}; "
        f"пропущено: {total_skipped}; "
        f"ошибки: {total_errors}; "
        f"упавших источников: {failed_sources}"
    )
    print("=" * 80)

    # Падение одного карьерного источника не должно ломать остальные.
    # Ненулевой код возвращаем только если не удалось запустить вообще ни один.
    return 1 if SOURCES and failed_sources == len(SOURCES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
