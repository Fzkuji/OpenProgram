"""Detect how OpenProgram is installed on this machine."""
from __future__ import annotations

import json
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
        if (
            (ancestor / ".git").exists()
            and package_root() == (ancestor / "openprogram").resolve()
        ):
            return ancestor
    return None


def is_pyinstaller_binary() -> bool:
    """True if running inside a PyInstaller-frozen executable."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def managed_runtime_root() -> Optional[Path]:
    """Return the verified product-runtime root for this interpreter.

    Desktop starts its worker with an explicit immutable-runtime marker, and
    release launchers set the same marker. Users and diagnostics can also
    invoke the bundled Python directly, so recognize that case from the
    release layout rather than relying on an inherited variable.

    Merely finding a file named ``runtime-manifest.json`` is not enough. The
    manifest must describe this exact interpreter and the product verifier
    and source manifest must be present beside it.
    """
    executable = Path(sys.executable).resolve()
    candidates: list[Path] = []
    for origin in (executable.parent, package_root()):
        for candidate in (origin, *origin.parents):
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        manifest_path = candidate / "runtime-manifest.json"
        if not (
            manifest_path.is_file()
            and (candidate / "product-runtime.json").is_file()
            and (candidate / "bin" / "verify-product-runtime.py").is_file()
        ):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            python_relative = manifest.get("python")
            if (
                not isinstance(manifest.get("schema"), int)
                or manifest["schema"] < 1
                or not isinstance(python_relative, str)
                or not python_relative
                or Path(python_relative).is_absolute()
            ):
                continue
            described_python = (candidate / python_relative).resolve()
        except (OSError, ValueError, TypeError):
            continue
        if described_python == executable and described_python.is_file():
            return candidate
    return None


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
    if managed_runtime_root() is not None:
        return InstallMethod.MANAGED_RELEASE
    if repo_root() is not None:
        return InstallMethod.SOURCE_CHECKOUT
    return InstallMethod.UNKNOWN
