"""Agents defined as Markdown files by other tools, read as read-only Agents.

Claude Code keeps sub-agents in ``.claude/agents/<name>.md`` (in a project
and in the home folder), and the cross-tool convention puts them in
``.agents/agents/<name>.md``. Each file is YAML frontmatter (``name``,
``description``, ``tools``, ``model``, ``isolation``) followed by the
system prompt. Kimi CLI reads its peers' agent files the same way, so a
project's agents work in every tool that opens it.

Such a file becomes an Agent other agents may start with
``agent(agent="<name>")``, listed in the agent tool's roster. Its id is
``md:<file stem>``. It is never written: the registry's writes read the
saved records only (``manager._read_agent``), so update, delete and
set-default report it as not found, and the person keeps editing the file
itself. To change it inside OpenProgram, duplicate it into a saved Agent.

What maps: ``tools`` (Claude Code names such as ``Read`` or ``Bash`` become
the matching tools; names with no match are dropped, and no field means
every tool), ``model`` only as ``provider/model`` (``sonnet`` and
``inherit`` leave the conversation's model), and ``isolation``. A file
without frontmatter, a name or a description is skipped.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from openprogram.runtime.agent_registry import manager

_log = logging.getLogger(__name__)

ID_PREFIX = "md:"
#: Folders searched under the project root and the home folder.
AGENT_DIRS = (".claude/agents", ".agents/agents")
MAX_FILES = 50
MAX_BYTES = 64_000

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_STEM = re.compile(r"[^a-z0-9_-]+")

# Claude Code's tool names, and the shared lowercase ones, to OpenProgram's.
_TOOL_NAMES = {
    "read": ["read"], "write": ["write"], "edit": ["edit"], "multiedit": ["edit"],
    "glob": ["glob"], "grep": ["grep"], "ls": ["list"], "list": ["list"],
    "bash": ["bash"], "webfetch": ["web_fetch"], "web_fetch": ["web_fetch"],
    "websearch": ["web_search"], "web_search": ["web_search"],
    "notebookedit": ["notebook_edit"], "todowrite": ["todo_create", "todo_update", "todo_list"],
    "task": ["agent"], "agent": ["agent"],
}


def is_import(agent_id: str) -> bool:
    return isinstance(agent_id, str) and agent_id.startswith(ID_PREFIX)


def _roots() -> list[Path]:
    """The project folder this turn works in (and the folders above it, up
    to its git root), then the home folder."""
    roots: list[Path] = []
    try:
        from openprogram.state.worktree.context import current_worktree_path
        base = current_worktree_path()
    except Exception:  # noqa: BLE001
        base = None
    if not base:
        try:
            from openprogram.state.paths import get_default_workdir
            base = get_default_workdir()
        except Exception:  # noqa: BLE001
            base = None
    home = Path(os.path.expanduser("~"))
    if base:
        here = Path(base).expanduser()
        for folder in (here, *here.parents):
            if folder == home:
                break
            roots.append(folder)
            if (folder / ".git").exists():
                break
    if home not in roots:
        roots.append(home)
    return roots


def _tools(raw: object) -> dict:
    if raw is None:
        return {"mode": "automatic"}
    names = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    allowed: list[str] = []
    for name in names:
        key = str(name).strip().split("(", 1)[0].strip().lower()
        for tool in _TOOL_NAMES.get(key, ()):
            if tool not in allowed:
                allowed.append(tool)
    return {"mode": "selected", "allowed": allowed} if allowed else {"mode": "none"}


def parse(path: Path) -> Optional[manager.AgentSpec]:
    """The read-only Agent ``path`` defines, or None when it defines none."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    try:
        import yaml
        meta = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 - not an agent file
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name") or "").strip()
    description = " ".join(str(meta.get("description") or "").split())
    stem = _STEM.sub("-", path.stem.lower()).strip("-")
    if not name or not description or not stem:
        return None
    model = str(meta.get("model") or "").strip()
    provider, _, model_id = model.partition("/") if "/" in model else ("", "", "")
    isolation = str(meta.get("isolation") or "").strip().lower()
    return manager.AgentSpec(
        id=f"{ID_PREFIX}{stem}", name=name, description=description,
        system_prompt=match.group(2).strip(), tools=_tools(meta.get("tools")),
        model=manager.AgentModelRef(provider=provider, id=model_id),
        thinking_effort="", delegation="on",
        isolation=isolation if isolation in ("worktree", "none") else "",
        memory=manager.validate_memory_config({"mode": "off"}),
        skills={"disabled": [], "allowed": [], "categories": []},
    )


def list_imports(roots: Optional[Iterable[Path]] = None) -> list[manager.AgentSpec]:
    """Every Markdown Agent found, the nearest folder first; a later file
    with an id already found is ignored (a project's own wins over home)."""
    found: dict[str, manager.AgentSpec] = {}
    for root in (list(roots) if roots is not None else _roots()):
        for folder in AGENT_DIRS:
            directory = Path(root) / folder
            try:
                files = sorted(directory.glob("*.md")) if directory.is_dir() else []
            except OSError:
                continue
            for path in files:
                if len(found) >= MAX_FILES:
                    return list(found.values())
                spec = parse(path)
                if spec is not None and spec.id not in found:
                    found[spec.id] = spec
    return list(found.values())


def get(agent_id: str) -> Optional[manager.AgentSpec]:
    if not is_import(agent_id):
        return None
    return next((spec for spec in list_imports() if spec.id == agent_id), None)


__all__ = ["AGENT_DIRS", "ID_PREFIX", "get", "is_import", "list_imports", "parse"]
