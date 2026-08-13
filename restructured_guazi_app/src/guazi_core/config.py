"""Configuration loading helpers.

Migrated from the original config_loader.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def load_config(name: str, config_dir: str | Path | None = None) -> dict[str, Any]:
    if config_dir is None:
        path = project_path("config", name)
    else:
        path = Path(config_dir) / name
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"Cannot parse {path}; install PyYAML or keep JSON-compatible YAML.") from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"Config {path} must contain an object")
        return data


def ensure_runtime_dirs() -> None:
    for path in [
        project_path("logs"),
        project_path("output"),
        project_path("artifacts", "screenshots"),
        project_path("artifacts", "debug"),
        project_path("knowledge_base"),
    ]:
        path.mkdir(parents=True, exist_ok=True)
