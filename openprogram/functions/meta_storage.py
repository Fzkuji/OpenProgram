"""Profile-scoped persistence for tool profiles and Functions UI metadata."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from openprogram.paths import get_active_profile, get_state_dir


FUNCTIONS_META = "functions_meta.json"
PROGRAMS_META = "programs_meta.json"


def _state_path(filename: str) -> Path:
    return get_state_dir() / filename


def _legacy_path(filename: str) -> Path:
    from openprogram.webui import server

    return Path(server.__file__).resolve().parent / filename


def load_meta(filename: str, default: dict[str, Any]) -> dict[str, Any]:
    """Read profile state, copying the old package-local file once."""
    state = _state_path(filename)
    if state.is_file():
        return json.loads(state.read_text(encoding="utf-8"))

    legacy = _legacy_path(filename)
    if get_active_profile() is None and legacy.is_file():
        data = json.loads(legacy.read_text(encoding="utf-8"))
        save_meta(filename, data)
        return data
    return default


def save_meta(filename: str, data: dict[str, Any]) -> None:
    path = _state_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_functions_meta(default: dict[str, Any]) -> dict[str, Any]:
    return load_meta(FUNCTIONS_META, default)


def save_functions_meta(data: dict[str, Any]) -> None:
    save_meta(FUNCTIONS_META, data)


def load_programs_meta(default: dict[str, Any]) -> dict[str, Any]:
    return load_meta(PROGRAMS_META, default)


def save_programs_meta(data: dict[str, Any]) -> None:
    save_meta(PROGRAMS_META, data)
