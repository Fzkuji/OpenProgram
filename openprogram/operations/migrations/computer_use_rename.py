"""One-time rewrite of a profile's stored names of the computer-use tool.

The tool ``computer_use_agent`` is now ``computer_use``, and the keys its
permission rules are written for changed with its call: ``app <application>``
is ``window <application>``, ``playwright_in_chrome`` is ``browser chrome``,
``machine <id> screen`` is ``<id> screen``. An Agent's tool lists, the global
and project permission rules are rewritten once; the marker is written last,
so an interrupted run starts over, and a name already rewritten is left alone.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from openprogram.host._platform.filesystem import replace_file
from openprogram.state import paths as state_paths

_log = logging.getLogger(__name__)

OLD, NEW = "computer_use_agent", "computer_use"
_RULE = re.compile(r"^\s*computer_use_agent\((.*)\)\s*$", re.S)


def _marker(state: Path) -> Path:
    return state / "migrations" / "computer-use-rename.done"


def rule_key(key: str) -> str:
    """A rule key in the present words."""
    text = key.strip()
    if text.startswith("app ") or text.startswith("app:"):
        return "window" + text[3:]
    if text in ("playwright_in_chrome", "playwright_in_edge"):
        return "browser " + text.removeprefix("playwright_in_")
    words = text.split()
    if len(words) >= 2 and words[0] == "machine":
        return " ".join(words[1:])
    return text


def rename(value: Any) -> Any:
    """``value`` with every stored name of the tool and its rules in the present words."""
    if isinstance(value, str):
        if value == OLD:
            return NEW
        match = _RULE.match(value)
        return f"{NEW}({rule_key(match.group(1))})" if match else value
    if isinstance(value, list):
        return [rename(item) for item in value]
    if isinstance(value, dict):
        return {key: rename(item) for key, item in value.items()}
    return value


def _rewrite_json(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    changed = rename(data)
    if changed == data:
        return False
    temporary = path.with_name(f".{path.name}.computer-use-rename")
    temporary.write_text(json.dumps(changed, indent=2, ensure_ascii=False), encoding="utf-8")
    replace_file(temporary, path)
    return True


def _rewrite_config() -> bool:
    from openprogram.state.config import read_config_with_revision, write_config

    cfg, revision = read_config_with_revision()
    tools = cfg.get("tools") if isinstance(cfg, dict) else None
    if not isinstance(tools, dict):
        return False
    changed = rename(tools)
    if changed == tools:
        return False
    write_config({**cfg, "tools": changed}, expected_revision=revision)
    return True


def _rewrite_projects() -> int:
    from openprogram.state.store.project import project_store

    count = 0
    for project in project_store.list_projects():
        settings = project_store.load_project_settings(project.id)
        changed = rename(settings)
        if changed != settings:
            project_store.save_project_settings(project.id, changed)
            count += 1
    return count


def run() -> bool:
    """Rewrite the stored names if this profile has not been rewritten. Returns whether anything changed."""
    state = state_paths.get_state_dir()
    marker = _marker(state)
    if marker.exists():
        return False
    changed = False
    agents = state / "agents"
    if agents.is_dir():
        for path in sorted(agents.glob("*/agent.json")):
            changed = _rewrite_json(path) or changed
    try:
        changed = _rewrite_config() or changed
    except Exception:  # noqa: BLE001
        _log.warning("computer-use rename: the global permission rules were not rewritten", exc_info=True)
        raise
    changed = bool(_rewrite_projects()) or changed
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n", encoding="utf-8")
    return changed


__all__ = ["rename", "rule_key", "run"]
