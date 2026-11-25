"""Model and voice settings for a standalone application.

An exported application never carries settings or credentials: the
bundle may be passed on. On the computer it runs on, each launch copies
the model and voice settings of that computer's OpenProgram (``source``,
``~/.openprogram`` unless the export named another) into the
application's own data folder, so a key changed in OpenProgram reaches
it on the next launch. Speech models are linked, not copied (they are
large). Without an OpenProgram there, the application uses what its data
folder already holds; its window says when speech or a model is missing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from openprogram.host._platform.filesystem import replace_file

#: What is taken from the source state folder.
COPIED = ("config.json", "auth")
LINKED = ("voice",)


def default_source() -> Path:
    return Path.home() / ".openprogram"


def sync(source: Path | None, data: Path) -> list[str]:
    """Copy the settings of ``source`` into ``data``; returns what was taken."""
    if source is None or not source.is_dir() or source.resolve() == data.resolve():
        return []
    data.mkdir(parents=True, exist_ok=True)
    taken: list[str] = []
    for name in COPIED:
        origin, target = source / name, data / name
        if origin.is_file():
            temp = target.with_name(f".{name}.copy")
            shutil.copy2(origin, temp)
            os.chmod(temp, 0o600)
            replace_file(temp, target)
            taken.append(name)
        elif origin.is_dir():
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target)
            shutil.copytree(origin, target, symlinks=False, dirs_exist_ok=True)
            os.chmod(target, 0o700)
            taken.append(name)
    for name in LINKED:
        origin, target = source / name, data / name
        if not origin.is_dir():
            continue
        if target.is_symlink() and Path(os.readlink(target)) == origin:
            taken.append(name)
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            continue  # the application keeps speech models of its own
        target.symlink_to(origin, target_is_directory=True)
        taken.append(name)
    return taken


__all__ = ["COPIED", "LINKED", "default_source", "sync"]
