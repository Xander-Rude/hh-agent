from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
RESUMES_CONFIG = DATA_DIR / "resumes.yaml"

# Одно и то же портфолио/презентация прикладывается ко всем карьерным откликам.
PRESENTATION_PATH = ATTACHMENTS_DIR / "Alexander_Rudenko.pdf"


def get_resume_file_path(resume_key: str | None) -> Path | None:
    """Возвращает локальный PDF выбранного резюме.

    В data/resumes.yaml для каждого резюме потребуется поле file_path, например:
        file_path: data/resumes/project_manager.pdf

    Относительные пути считаются от корня проекта.
    """
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


def validate_application_assets(resume_key: str | None) -> tuple[Path, Path]:
    resume_path = get_resume_file_path(resume_key)

    if resume_path is None:
        raise FileNotFoundError(
            f"Для резюме {resume_key!r} не настроен file_path в data/resumes.yaml"
        )

    if not resume_path.exists():
        raise FileNotFoundError(f"Файл резюме не найден: {resume_path}")

    if not PRESENTATION_PATH.exists():
        raise FileNotFoundError(
            f"Файл презентации не найден: {PRESENTATION_PATH}"
        )

    return resume_path, PRESENTATION_PATH
