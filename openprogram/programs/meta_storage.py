"""Profile-scoped persistence for tool profiles and Functions UI metadata."""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from openprogram.paths import get_active_profile, get_state_dir

logger = logging.getLogger(__name__)


FUNCTIONS_META = "functions_meta.json"
PROGRAMS_META = "programs_meta.json"


def _state_path(filename: str) -> Path:
    return get_state_dir() / filename


def _legacy_path(filename: str) -> Path:
    import openprogram.webui as compatibility_webui

    package_file = compatibility_webui.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent / filename


def _read_meta_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.warning("ignoring damaged meta file %s", path)
        return None
    if not isinstance(data, dict):
        logger.warning("ignoring damaged meta file %s", path)
        return None
    return data


def load_meta(filename: str, default: dict[str, Any]) -> dict[str, Any]:
    """Read profile state, copying the old package-local file once."""
    state = _state_path(filename)
    if state.is_file():
        return _read_meta_object(state) or {}

    legacy = _legacy_path(filename)
    if get_active_profile() is None and legacy.is_file():
        data = _read_meta_object(legacy)
        if data is None:
            return {}
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


# Icon values stored before the emoji → flat-icon switch. Rewritten to
# slugs once, on read, so every consumer sees slugs only.
_EMOJI_TO_SLUG = {
    "📦": "box", "🤖": "bot", "🌐": "earth", "🔍": "telescope",
    "📚": "scan-text", "🖥": "cpu", "📄": "scan-text",
    "📊": "chart-column", "🎨": "pen-tool", "✏️": "feather",
    "🛠": "hammer", "⚡": "gauge", "💡": "atom", "🔥": "flame",
    "⭐": "compass", "🎯": "route", "📷": "eye", "🎵": "mic",
    "🧠": "atom", "💬": "mic", "🎮": "rocket", "🚀": "rocket",
    "🧪": "atom", "✨": "flame",
}


def load_programs_meta(default: dict[str, Any]) -> dict[str, Any]:
    data = load_meta(PROGRAMS_META, default)
    favorites = data.get("favorites")
    if isinstance(favorites, list):
        from openprogram.programs._programs import KNOWN_PROGRAMS

        aliases = {
            alias: program.function
            for program in KNOWN_PROGRAMS
            for alias in (program.install_dir, program.package, program.repo_dir_name)
        }
        rewritten_favorites = list(dict.fromkeys(
            aliases.get(name, name) for name in favorites if isinstance(name, str)
        ))
        if rewritten_favorites != favorites:
            data["favorites"] = rewritten_favorites
    icons = data.get("icons")
    if isinstance(icons, dict):
        rewritten = {
            name: _EMOJI_TO_SLUG.get(value, value)
            for name, value in icons.items()
        }
        if rewritten != icons:
            data["icons"] = rewritten
    if data.get("favorites") != favorites or data.get("icons") != icons:
        save_programs_meta(data)
    return data


def save_programs_meta(data: dict[str, Any]) -> None:
    save_meta(PROGRAMS_META, data)
