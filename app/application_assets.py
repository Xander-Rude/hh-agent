from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
RESUMES_DIR = DATA_DIR / "resumes"
RESUMES_CONFIG = DATA_DIR / "resumes.yaml"

PRESENTATION_PATH = ATTACHMENTS_DIR / "Alexander_Rudenko.pdf"


def _normalize(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return " ".join(text.split())


def _configured_resume_path(resume_key: str | None) -> Path | None:
    if not resume_key or not RESUMES_CONFIG.exists():
        return None

    config = yaml.safe_load(
        RESUMES_CONFIG.read_text(encoding="utf-8")
    ) or {}

    resumes = dict(config.get("resumes", {}))
    for index, item in enumerate(config.get("generated_resumes", [])):
        key = item.get("key", f"generated_{index}")
        resumes[key] = item

    resume = resumes.get(resume_key)
    if not resume:
        return None

    raw_path = str(resume.get("file_path") or "").strip()
    if not raw_path:
        return None

    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path

    return path.resolve()


def get_resume_file_path(
    resume_key: str | None,
    resume_title: str | None = None,
) -> Path | None:
    configured = _configured_resume_path(resume_key)
    if configured is not None:
        return configured

    if not RESUMES_DIR.exists():
        return None

    candidates = sorted(RESUMES_DIR.glob("*.pdf"))
    if not candidates:
        return None

    probe = _normalize(f"{resume_key or ''} {resume_title or ''}")

    aliases = [
        (
            ("delivery", "program"),
            "Delivery Manager & Program Manager.pdf",
        ),
        (
            ("product",),
            "Technical Product Manager & Руководитель разработки продукта.pdf",
        ),
        (
            ("technical project", "техническ"),
            "Technical Project Manager & Руководитель технических проектов.pdf",
        ),
        (
            ("project", "проект"),
            "Руководитель IT-проектов & Senior Project Manager.pdf",
        ),
    ]

    for markers, filename in aliases:
        if any(marker in probe for marker in markers):
            path = RESUMES_DIR / filename
            if path.exists():
                return path.resolve()

    probe_tokens = set(probe.split())
    scored: list[tuple[int, Path]] = []

    for path in candidates:
        name_tokens = set(_normalize(path.stem).split())
        score = len(probe_tokens & name_tokens)
        scored.append((score, path))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1].resolve()

    return candidates[0].resolve() if len(candidates) == 1 else None


def validate_application_assets(
    resume_key: str | None,
    resume_title: str | None = None,
) -> tuple[Path, Path]:
    resume_path = get_resume_file_path(
        resume_key,
        resume_title,
    )

    if resume_path is None:
        raise FileNotFoundError(
            "Не удалось определить PDF выбранного резюме в data/resumes"
        )

    if not resume_path.exists():
        raise FileNotFoundError(f"Файл резюме не найден: {resume_path}")

    if not PRESENTATION_PATH.exists():
        raise FileNotFoundError(
            f"Файл презентации не найден: {PRESENTATION_PATH}"
        )

    return resume_path, PRESENTATION_PATH
