"""``openprogram programs export``: an application package as software of its own.

The result is a folder (``--format dir``) or, on macOS, an ``.app``
(``--format app``) holding

- ``app/`` — a copy of the package (application.json, backend, built window);
- ``python/`` — when the Python that runs OpenProgram can be moved (a
  standalone CPython, as the App's runtime is): that interpreter and its
  libraries with OpenProgram, trimmed of what one application never uses
  (the App's web front end, the other built-in applications, the browser
  automation's drivers, CPython's tests and Tk). Otherwise the launcher
  names the interpreter that ran the export (``--python reference``), which
  then must stay where it is;
- a launcher (``Contents/MacOS/<name>``, or ``run.sh`` and ``run.cmd``)
  that starts :mod:`openprogram.interfaces.standalone.server` with the application's data folder and the
  settings folder to take models and voice from.

Nothing of the exporting computer's state is copied: no settings, keys,
recordings or conversations. Applications that declare ``chat`` (they need
the App's composer) or have project scope (they need the App's projects)
are refused.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import sys
from pathlib import Path

from openprogram.programs._host.ui import catalog

FORMATS = ("app", "dir")
PYTHONS = ("auto", "bundle", "reference")

#: Left out of the copied interpreter: (folder relative to the interpreter's
#: prefix or to site-packages, why).
TRIMMED_PREFIX = ("include", "share", "lib/tcl9", "lib/tcl9.0", "lib/tk9.0", "lib/itcl4.3.5", "lib/thread3.0.4")
TRIMMED_STDLIB = ("test", "idlelib", "tkinter", "turtledemo")
TRIMMED_SITE = ("playwright", "PyObjCTest")
#: The largest icon PNG an export takes.
ICON_LIMIT = 4 * 1024 * 1024
#: Inside the ``openprogram`` package.
TRIMMED_OPENPROGRAM = ("interfaces/server/_frontend",)


def resolve_package(name: str) -> Path:
    """The package folder for a path, a built-in application or an installed one."""
    path = Path(name).expanduser()
    if (path / "application.json").is_file():
        return path.resolve()
    from openprogram.programs._host.registry import _builtin_applications_root
    builtin = Path(_builtin_applications_root()) / name
    if (builtin / "application.json").is_file():
        return builtin.resolve()
    row = catalog.record(name)
    if row is not None:
        return catalog.source_root(row).resolve()
    raise FileNotFoundError(f"{name} is neither an application folder nor an application id")


def check_exportable(definition: dict) -> None:
    if "chat" in definition["capabilities"]:
        raise ValueError(f"{definition['id']} declares chat: it needs the App's composer and cannot run on its own")
    if definition["scope"] != "global":
        raise ValueError(f"{definition['id']} has project scope: it needs the App's projects and cannot run on its own")


def movable_python() -> Path | None:
    """The prefix of a Python that can be copied: a standalone CPython with OpenProgram in it.

    The running interpreter when it is one (the App's runtime), else the
    runtime of this profile's App (``<state>/runtime``).
    """
    candidates = [Path(sys.prefix)] if sys.prefix == sys.base_prefix else []
    try:
        from openprogram.state.paths import get_state_dir
        manifest = get_state_dir() / "runtime" / "runtime-manifest.json"
        python = manifest.parent / json.loads(manifest.read_text(encoding="utf-8"))["python"]
        candidates.append(python.resolve().parent.parent)
    except (OSError, ValueError, KeyError):
        pass
    for prefix in candidates:
        if (prefix / "BUILD").exists() or prefix.name.startswith("cpython-"):
            if any((prefix / "lib").glob("python3*/site-packages/openprogram")) or \
                    (prefix / "Lib" / "site-packages" / "openprogram").is_dir():
                return prefix
    return None


def _interpreter(prefix: Path) -> Path:
    for relative in ("bin/python3", "python.exe"):
        if (prefix / relative).exists():
            return Path(relative)
    raise FileNotFoundError(f"no interpreter in {prefix}")


def _copy_python(prefix: Path, target: Path) -> None:
    """Copy the interpreter at ``prefix`` to ``target``, trimmed, with this OpenProgram."""
    site = next(iter(sorted((prefix / "lib").glob("python3*/site-packages"))), None) or prefix / "Lib" / "site-packages"
    stdlib = site.parent
    skip = {prefix / p for p in TRIMMED_PREFIX} | {stdlib / p for p in TRIMMED_STDLIB} | \
        {site / p for p in TRIMMED_SITE} | {site / "openprogram"}

    def ignore(folder: str, names: list[str]) -> set[str]:
        here = Path(folder)
        return {n for n in names if here / n in skip}

    shutil.copytree(prefix, target, symlinks=True, ignore=ignore)
    # OpenProgram itself: the code that ran this export, not what the runtime last held.
    import openprogram
    source = Path(openprogram.__file__).parent
    relative_site = site.relative_to(prefix)
    _copy_openprogram(source, target / relative_site / "openprogram")


def _copy_openprogram(source: Path, target: Path) -> None:
    applications = source / "programs" / "applications"
    skip = {source / p for p in TRIMMED_OPENPROGRAM}

    def ignore(folder: str, names: list[str]) -> set[str]:
        here = Path(folder)
        left = {n for n in names if here / n in skip or n in ("__pycache__", "node_modules")}
        if here == applications:  # the other built-in applications
            left |= {n for n in names if n != "__init__.py"}
        return left

    shutil.copytree(source, target, symlinks=False, ignore=ignore)


def _copy_package(package: Path, definition: dict, target: Path) -> None:
    """The package without what only builds it: a ``web/`` source folder
    beside the built window, caches and checkouts."""
    left = ["__pycache__", "node_modules", ".git", ".venv", "*.pyc"]
    window = Path(definition["ui"].get("root") or ".") / definition["ui"]["entry"]
    if window.parts[0] != "web":
        left.append("web")
    shutil.copytree(package, target, ignore=shutil.ignore_patterns(*left))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "", text).strip() or "Application"


def _home_relative(path: Path) -> str:
    """``path`` for a shell, ``$HOME``-relative when it is under the home folder."""
    try:
        return '$HOME/' + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _shell_launcher(*, python: str, package: str, app_id: str, settings: str) -> str:
    return f"""#!/bin/sh
# Starts the application's server unless it runs and opens its window.
here="$(cd "$(dirname "$0")" && pwd)"
python={python}
package="$(cd {package} && pwd)"
case "$(uname)" in
  Darwin) data="${{OPENPROGRAM_APPLICATION_DATA:-$HOME/Library/Application Support/OpenProgram Applications/{app_id}}}" ;;
  *) data="${{OPENPROGRAM_APPLICATION_DATA:-${{XDG_DATA_HOME:-$HOME/.local/share}}/openprogram-applications/{app_id}}}" ;;
esac
settings="{settings}"
[ -d "$settings" ] || settings="$HOME/.openprogram"
export OPENPROGRAM_DATA_DIR="$data" OPENPROGRAM_STANDALONE_APPLICATION="$package" PYTHONNOUSERSITE=1
exec "$python" -m openprogram.interfaces.standalone launch --package "$package" --data "$data" --settings-from "$settings" "$@"
"""


def _cmd_launcher(*, python: str, app_id: str) -> str:
    return f"""@echo off
rem Starts the application's server unless it runs and opens its window.
set "here=%~dp0"
set "data=%LOCALAPPDATA%\\OpenProgram Applications\\{app_id}"
if defined OPENPROGRAM_APPLICATION_DATA set "data=%OPENPROGRAM_APPLICATION_DATA%"
set "settings=%USERPROFILE%\\.openprogram"
set "OPENPROGRAM_DATA_DIR=%data%"
set "OPENPROGRAM_STANDALONE_APPLICATION=%here%app"
set PYTHONNOUSERSITE=1
{python} -m openprogram.interfaces.standalone launch --package "%here%app" --data "%data%" --settings-from "%settings%" %*
"""


def _write_icon(png: bytes, resources: Path) -> str | None:
    """``AppIcon.icns`` in ``resources`` from a square PNG (macOS ``sips`` and ``iconutil``)."""
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > ICON_LIMIT:
        raise ValueError("the icon must be a PNG of at most 4 MB")
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "icon.png"
        source.write_bytes(png)
        iconset = Path(temp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(size * scale), str(size * scale), str(source), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "AppIcon.icns")],
                       check=True, capture_output=True)
    return "AppIcon"


def export(name: str, output: Path, *, format: str = "app", python: str = "auto", next_free: bool = False,
           icon: bytes | None = None) -> dict:
    """Write the standalone application for ``name`` into ``output``; returns what was written.

    An existing export of that name is refused, or, with ``next_free``, kept
    beside the new one, numbered as the Finder numbers a second download
    (``Meetings 2.app``). ``icon`` is the application's icon as a square
    PNG (the App draws it); an .app without one shows the system's.
    """
    if format not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    if python not in PYTHONS:
        raise ValueError(f"python must be one of {', '.join(PYTHONS)}")
    if format == "app" and sys.platform != "darwin":
        raise ValueError("an .app is made on macOS; use --format dir here")
    package = resolve_package(name)
    definition = catalog.manifest(package)
    check_exportable(definition)
    title = _slug(definition.get("title") or definition["id"])
    prefix = movable_python() if python in ("auto", "bundle") else None
    if python == "bundle" and prefix is None:
        raise ValueError("this OpenProgram does not run on a Python that can be copied; use --python reference")

    output = output.expanduser().resolve()
    named = (lambda n: f"{n}.app") if format == "app" else (lambda n: n)
    target = output / named(title)
    number = 2
    while next_free and target.exists():
        target, number = output / named(f"{title} {number}"), number + 1
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    root = target / "Contents" / "Resources" if format == "app" else target
    root.mkdir(parents=True)
    try:
        _copy_package(package, definition, root / "app")
        if prefix is not None:
            _copy_python(prefix, root / "python")
            interpreter = root / "python" / _interpreter(prefix)
            launcher_python = '"$here/' + os.path.relpath(interpreter, target / "Contents" / "MacOS" if format == "app" else target) + '"'
            cmd_python = '"%here%python\\python.exe"'
        else:
            launcher_python = f'"{Path(sys.executable).as_posix()}"'
            cmd_python = f'"{sys.executable}"'
        from openprogram.state.paths import get_state_dir
        settings = _home_relative(get_state_dir())
        if format == "app":
            macos = target / "Contents" / "MacOS"
            macos.mkdir()
            executable = macos / title.replace(" ", "")
            executable.write_text(_shell_launcher(python=launcher_python, package='"$here/../Resources/app"',
                                                  app_id=definition["id"], settings=settings), encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            icon_file = _write_icon(icon, root) if icon else None
            with open(target / "Contents" / "Info.plist", "wb") as handle:
                plistlib.dump({
                    **({"CFBundleIconFile": icon_file} if icon_file else {}),
                    "CFBundleName": title,
                    "CFBundleDisplayName": definition.get("title") or title,
                    "CFBundleIdentifier": f"org.openprogram.application.{re.sub(r'[^A-Za-z0-9.-]', '-', definition['id'])}",
                    "CFBundleExecutable": executable.name,
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": str(definition.get("version") or "1.0"),
                    "CFBundleVersion": str(definition.get("version") or "1.0"),
                    "LSMinimumSystemVersion": "13.0",
                    "NSHighResolutionCapable": True,
                    "NSMicrophoneUsageDescription": "Records your microphone when the application records.",
                    "NSAudioCaptureUsageDescription": "Records the computer's sound when the application records.",
                }, handle)
        else:
            run = target / "run.sh"
            run.write_text(_shell_launcher(python=launcher_python, package='"$here/app"',
                                           app_id=definition["id"], settings=settings), encoding="utf-8")
            run.chmod(run.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (target / "run.cmd").write_text(_cmd_launcher(python=cmd_python, app_id=definition["id"]),
                                            encoding="utf-8", newline="\r\n")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"path": str(target), "application": definition["id"], "format": format,
            "python": "bundle" if prefix is not None else "reference",
            "size": sum(p.stat().st_size for p in target.rglob("*") if p.is_file() and not p.is_symlink())}


__all__ = ["FORMATS", "PYTHONS", "check_exportable", "export", "movable_python", "resolve_package"]
