"""The hosts named in the user's SSH configuration, for Add device → SSH server.

Reads ``~/.ssh/config`` and the files its ``Include`` lines name (relative
paths are under ``~/.ssh``, globs expand), and returns each concrete
``Host`` alias in file order: patterns (``*``, ``?``, ``!``) are not hosts
one can connect to. Nothing is resolved or contacted; ``ssh`` itself reads
the rest of each entry when the device is used.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

_MAX_FILES = 32


def _expand(pattern: str, base: Path) -> list[Path]:
    pattern = os.path.expanduser(pattern)
    if not os.path.isabs(pattern):
        pattern = str(base / pattern)
    return [Path(found) for found in sorted(glob.glob(pattern))]


def config_hosts(path: Path | None = None) -> list[str]:
    """Concrete ``Host`` aliases from the SSH configuration, without duplicates."""
    home_ssh = Path.home() / ".ssh"
    pending = [path or home_ssh / "config"]
    seen_files: set[Path] = set()
    hosts: list[str] = []
    while pending and len(seen_files) < _MAX_FILES:
        current = pending.pop(0)
        try:
            resolved = current.resolve()
            if resolved in seen_files or not resolved.is_file():
                continue
            seen_files.add(resolved)
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, rest = line.replace("=", " ", 1).partition(" ")
            key = key.lower()
            values = rest.split()
            if key == "include":
                for value in values:
                    pending.extend(_expand(value, home_ssh))
            elif key == "host":
                for value in values:
                    if any(ch in value for ch in "*?!") or value in hosts:
                        continue
                    hosts.append(value)
    return hosts


__all__ = ["config_hosts"]
