"""Scan command source directories and yield parsed entries.

Phase-1 scope: user (L4) + project (L5) layers. Plugins (L1), MCP
(L2) and skills (L3) plug into the registry through other adapters,
not this scanner.

Path safety: ``realpath`` is resolved and the result must live under
one of the trusted roots (user state dir or current cwd). Symlinks
pointing outside are dropped. Directory traversal in glob patterns
(``..``) is prevented by anchoring ``Path.rglob`` at the trusted root.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .frontmatter import ParsedCommand, parse


@dataclass
class LoadedCommand:
    spec: ParsedCommand
    source: str                # "user" | "project"
    source_label: str          # short tag for the UI ("(user)", "(project)")
    path: str                  # absolute file path (for diagnostics)


def user_commands_dir() -> Path:
    """User-level commands live next to plugins under
    ``~/.openprogram/``, not under the profile-specific state dir —
    user-authored prompt templates are tied to the human, not to a
    profile."""
    from openprogram.plugins.paths import root as _plugins_root
    return _plugins_root() / "commands"


def project_commands_dir(cwd: Path | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / ".openprogram" / "commands"


def _safe_iter(root: Path) -> Iterable[Path]:
    """Yield ``.md`` files under ``root`` whose realpath stays inside
    ``root``. Drops anything that resolves outside (symlink attacks)."""
    if not root.is_dir():
        return
    root_real = root.resolve()
    for p in sorted(root.rglob("*.md")):
        try:
            real = p.resolve()
        except OSError:
            continue
        try:
            real.relative_to(root_real)
        except ValueError:
            continue
        yield p


def _load_dir(root: Path, source: str, label: str) -> list[LoadedCommand]:
    out: list[LoadedCommand] = []
    for md in _safe_iter(root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        spec = parse(text, default_name=md.stem)
        out.append(LoadedCommand(
            spec=spec, source=source, source_label=label, path=str(md.resolve()),
        ))
    return out


def load_user() -> list[LoadedCommand]:
    return _load_dir(user_commands_dir(), "user", "(user)")


def load_project(cwd: Path | None = None) -> list[LoadedCommand]:
    return _load_dir(project_commands_dir(cwd), "project", "(project)")


def ensure_user_dir() -> Path:
    """Create ``~/.openprogram/commands/`` if it doesn't exist. The
    web UI calls this when the user first opens the commands page so
    a fresh install has a place to drop ``.md`` files into."""
    d = user_commands_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
