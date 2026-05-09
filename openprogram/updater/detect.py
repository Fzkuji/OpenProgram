"""Detect how OpenProgram is installed on this machine."""
from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path
from typing import Optional


class InstallMethod(str, Enum):
    GIT_CHECKOUT = "git_checkout"  # ``pip install -e <clone>`` from a git repo
    BINARY = "binary"              # PyInstaller / Nuitka standalone executable
    PIP_WHEEL = "pip_wheel"        # ``pip install openprogram`` from PyPI / wheel
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
    """Best-effort classification of the install."""
    if is_pyinstaller_binary():
        return InstallMethod.BINARY
    if repo_root() is not None:
        return InstallMethod.GIT_CHECKOUT
    # Heuristic: if the package dir lives under a site-packages tree it's
    # almost certainly a wheel install.
    pkg = package_root()
    if "site-packages" in pkg.parts or "dist-packages" in pkg.parts:
        return InstallMethod.PIP_WHEEL
    return InstallMethod.UNKNOWN
