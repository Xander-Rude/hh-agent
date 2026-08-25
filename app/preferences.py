from pathlib import Path

import yaml


def load_preferences(path: str = "data/preferences.yaml") -> dict:
    content = Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(content)