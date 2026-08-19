"""Detect how OpenProgram is installed on this machine."""
from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path
from typing import Optional


class InstallMethod(str, Enum):
    MANAGED_RELEASE = "managed_release"
    SOURCE_CHECKOUT = "source_checkout"
    UNKNOWN = "unknown"


def package_root() -> Path:
    """Filesystem location of the installed openprogram package."""
    import openprogram
    p = Path(openprogram.__file__).resolve().parent
    return p


def repo_root() -> Optional[Path]:
    """Return the git working tree containing this install, or None.

    For an editable install (``pip install -e``) the package directory
    sits inside the git checkout; we walk up from there until we find a
    ``.git`` directory. For wheel installs there is no ``.git`` anywhere
    along that path and we return None.
    """
    cur = package_root().parent  # parent of openprogram/ → repo root candidate
    for ancestor in [cur, *cur.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    return None


def is_pyinstaller_binary() -> bool:
    """True if running inside a PyInstaller-frozen executable."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def detect_install_method() -> InstallMethod:
    """Classify the product update path.

    Release launchers set ``OPENPROGRAM_IMMUTABLE_RUNTIME=1``.  Check that
    first because their Python package naturally lives in site-packages and a
    packaged Desktop may also be launched from a source worktree during tests.
    """
    if os.environ.get("OPENPROGRAM_IMMUTABLE_RUNTIME", "").strip() in {
        "1", "true", "True", "yes",
    }:
        return InstallMethod.MANAGED_RELEASE
    if is_pyinstaller_binary():
        return InstallMethod.MANAGED_RELEASE
    if repo_root() is not None:
        return InstallMethod.SOURCE_CHECKOUT
    return InstallMethod.UNKNOWN
