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


class UpdateRoute(str, Enum):
    """Which way a new version reaches this installation."""

    VERSION_STORE = "version_store"
    DESKTOP_APP = "desktop_app"
    COMMAND_LINE_RELEASE = "command_line_release"
    SOURCE_CHECKOUT = "source_checkout"
    REINSTALL = "reinstall"


INSTALL_GUIDE = "https://github.com/FuzzyKuji/OpenProgram/blob/main/docs/install/install.html"
UPDATE_GUIDANCE = {
    UpdateRoute.DESKTOP_APP: (
        "This copy runs inside the desktop App, so the App updates it. "
        "Open Settings in the App and use Check for updates."
    ),
    UpdateRoute.REINSTALL: (
        "This copy was installed some other way, for example with pip, so it cannot update itself. "
        f"Install the latest version by following {INSTALL_GUIDE}"
    ),
}


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
    manifest must describe this exact interpreter or its named worker launcher,
    and the product verifier
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
        if not described_python.is_relative_to(candidate) or not described_python.is_file():
            continue
        if described_python == executable:
            return candidate
        worker_relative = manifest.get("worker_python")
        if isinstance(worker_relative, str) and worker_relative and not Path(worker_relative).is_absolute():
            worker = (candidate / worker_relative).resolve()
            if worker.is_relative_to(candidate) and worker == executable and worker.is_file():
                return candidate
    return None


def _in_version_store() -> bool:
    from openprogram.operations.self_update.versions.layout import managed

    return managed()


def _inside_desktop_app(runtime_root: Path) -> bool:
    """An App keeps its runtime in Electron's resources directory; a CLI install never does."""
    return runtime_root.name == "runtime" and runtime_root.parent.name.lower() == "resources"


def update_route() -> UpdateRoute:
    """How this installation gets a new version."""
    if _in_version_store():
        return UpdateRoute.VERSION_STORE
    runtime = managed_runtime_root()
    if runtime is not None and _inside_desktop_app(runtime):
        return UpdateRoute.DESKTOP_APP
    method = detect_install_method()
    if method is InstallMethod.MANAGED_RELEASE:
        return UpdateRoute.COMMAND_LINE_RELEASE
    if method is InstallMethod.SOURCE_CHECKOUT:
        return UpdateRoute.SOURCE_CHECKOUT
    return UpdateRoute.REINSTALL


def detect_install_method() -> InstallMethod:
    """Classify the product update path.

    An App's embedded runtime sets ``OPENPROGRAM_IMMUTABLE_RUNTIME=1`` and a
    sealed version is recognised from where it runs.  Check those first because
    their Python package naturally lives in site-packages and a packaged
    Desktop may also be launched from a source worktree during tests.
    """
    if os.environ.get("OPENPROGRAM_IMMUTABLE_RUNTIME", "").strip() in {
        "1", "true", "True", "yes",
    }:
        return InstallMethod.MANAGED_RELEASE
    if _in_version_store():
        return InstallMethod.MANAGED_RELEASE
    if is_pyinstaller_binary():
        return InstallMethod.MANAGED_RELEASE
    if managed_runtime_root() is not None:
        return InstallMethod.MANAGED_RELEASE
    if repo_root() is not None:
        return InstallMethod.SOURCE_CHECKOUT
    return InstallMethod.UNKNOWN
