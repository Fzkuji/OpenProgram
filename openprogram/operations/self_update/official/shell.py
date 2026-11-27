"""The App shell as a layer: what a ``shell-<platform>`` layer holds, and the App it replaces.

The Release workflow turns each App into a layer (``publish.build_shell_layer``):
the files of ``OpenProgram.app`` on macOS, of the install folder on Windows,
byte for byte as signed and notarized, with their modes and links; on Linux
the AppImage, one file (:data:`APPIMAGE`). Code
signatures live in the files themselves (Mach-O signatures,
``Contents/_CodeSignature``, the stapled ticket in ``Contents/CodeResources``,
Authenticode in each PE file), so no extended attribute is carried; empty
directories, which a tree cannot hold, are listed in the layer's meta.

On an installed App (:mod:`.assemble`), a shell layer newer than the running
App is assembled from the running App's unchanged files and the downloaded
objects in a folder beside the App (:func:`staging_root`), on the App's own
drive, so the swap is a rename however the App and its data are spread over
drives; it is checked here (:func:`verify`) and recorded in
``<runtime>/shell/staged.json``. Restart to update swaps it in
(``apps/desktop/main/self-update/app-replace.js``). Code that needs the new
shell runs only in it: a plain ``upgrade`` stops once the App is staged, and
only ``upgrade --prepare`` builds and holds that code
(``runtime_layout.preparing_for_restart``), which the new App switches to on
its first start, after the swap.

A deb is not covered: it lives under ``/opt``, which only root may write, so it
keeps its installer route.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

from openprogram.host._platform.filesystem import replace_file
from openprogram.host._platform.runtime_layout import Layout

from .repository import ReleaseError

STAGED = "staged.json"
#: The platforms whose App replaces itself from a shell layer.
SYSTEMS = ("macos", "windows", "linux")
#: The one file of a Linux shell layer.
APPIMAGE = "OpenProgram.AppImage"
APP_PATH = "OPENPROGRAM_APP_PATH"
SHELL_VERSION = "OPENPROGRAM_SHELL_VERSION"
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")


def version_key(value: object) -> tuple[int, ...]:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        return ()
    parts = [int(part) for part in value.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def newer(candidate: object, running: object) -> bool:
    have, want = version_key(running), version_key(candidate)
    return bool(have) and bool(want) and want > have


# --- Reading an App ---------------------------------------------------------------


def system_of(platform: str) -> str:
    return platform.split("-", 1)[0]


def executable(app: Path, system: str) -> str:
    """The App's main executable, relative to its root."""
    if system == "macos":
        try:
            with open(app / "Contents" / "Info.plist", "rb") as stream:
                name = plistlib.load(stream).get("CFBundleExecutable")
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ReleaseError(f"{app} has no readable Info.plist: {exc}") from exc
        if not isinstance(name, str) or "/" in name or not name:
            raise ReleaseError(f"{app} names no executable")
        return f"Contents/MacOS/{name}"
    if system == "windows":
        return "OpenProgram.exe"
    if system == "linux":
        return APPIMAGE
    raise ReleaseError(f"an App shell on {system} is not a layer")


def bundle_version(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as stream:
            value = plistlib.load(stream).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) else None


def seed_python_minor(seed: Path) -> str | None:
    """The Python line of the launcher in a seed, which ``identity/python-minor`` takes from it."""
    try:
        if (seed / "python-minor").is_file():
            return (seed / "python-minor").read_text(encoding="ascii").strip() or None
        base = json.loads((seed / "seed.json").read_text(encoding="utf-8")).get("base")
    except (OSError, ValueError, AttributeError):
        return None
    match = re.match(r"py(\d+\.\d+)-", str(base or ""))
    return match[1] if match else None


def python_minor(app: Path) -> str | None:
    """The Python line of the launcher in the App's seed."""
    for seed in (app / "Contents" / "Resources" / "seed", app / "resources" / "seed"):
        minor = seed_python_minor(seed)
        if minor:
            return minor
    return None


def empty_directories(app: Path) -> list[str]:
    """Directories with nothing in them, which a layer's trees cannot hold."""
    found = []
    for current, directories, names in os.walk(app):
        if not directories and not names and Path(current) != app:
            found.append(Path(current).relative_to(app).as_posix())
    return sorted(found)


def unsupported_files(app: Path) -> list[str]:
    """Anything that is neither a file, a directory nor a link: a layer cannot carry it."""
    found = []
    for current, directories, names in os.walk(app):
        for name in [*directories, *names]:
            path = Path(current) / name
            if path.is_symlink() or path.is_dir() or path.is_file():
                continue
            found.append(path.relative_to(app).as_posix())
    return found


# --- The running App ------------------------------------------------------------------


@dataclass(frozen=True)
class RunningApp:
    """The App that started this update, when it can replace itself from a shell layer."""

    root: Path  # The .app bundle, the Windows install folder, or the AppImage file.
    version: str
    system: str
    team: str | None = None  # The macOS Developer ID team that signed it.


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def staging_root(app: RunningApp) -> Path:
    """Where a new App is assembled and waits: a hidden folder beside the App, on its drive."""
    return app.root.parent / f".{app.root.name}.update"


def staged_target(app: RunningApp) -> Path:
    """Where the checked new App waits for Restart to update: the swap renames it over ``app.root``."""
    return staging_root(app) / "staged" / app.root.name


def mac_team(bundle: Path) -> str | None:
    """The Developer ID team that signed a bundle, or ``None`` for an ad-hoc or local signature."""
    try:
        done = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(bundle)], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"^TeamIdentifier=(.+)$", f"{done.stderr}\n{done.stdout}", re.MULTILINE)
    team = match[1].strip() if match else ""
    return team if team and team != "not set" else None


def running_app(platform: str, environ: Mapping[str, str] | None = None) -> RunningApp | None:
    """The App that asked for this update (``OPENPROGRAM_APP_PATH``, ``OPENPROGRAM_SHELL_VERSION``).

    ``None`` from the command line, for a deb, and for an App that cannot
    replace itself: a translocated, read-only or locally built macOS App, or a
    Windows install folder or AppImage whose folder it cannot write. An AppImage
    is named by ``APPIMAGE``, which its runtime sets.
    """
    environ = os.environ if environ is None else environ
    folder, version = environ.get(APP_PATH, ""), environ.get(SHELL_VERSION, "")
    system = system_of(platform)
    if not folder or _VERSION.fullmatch(version) is None or system not in SYSTEMS:
        return None
    if system == "linux":
        image = Path(environ.get("APPIMAGE", ""))
        if not image.is_absolute() or not image.is_file() or not _writable(image.parent):
            return None
        return RunningApp(image, version, system)
    if system == "macos":
        executables = Path(folder)
        root = executables.parent.parent
        if executables.name != "MacOS" or root.suffix != ".app" or "/AppTranslocation/" in str(root) \
                or str(root).startswith("/Volumes/"):
            return None
        if not (_writable(root.parent) and _writable(root)):
            return None
        team = mac_team(root)
        return RunningApp(root, version, system, team) if team else None
    root = Path(folder)
    if not (root / "OpenProgram.exe").is_file() or not _writable(root.parent):
        return None
    return RunningApp(root, version, system)


# --- The staged App --------------------------------------------------------------------


def read_staged(layout: Layout) -> dict | None:
    try:
        value = json.loads((layout.shell / STAGED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def forget(layout: Layout) -> None:
    """Drop the staged App and its record."""
    staged = read_staged(layout)
    path = Path(str((staged or {}).get("path") or ""))
    if staged and path.parent.name == "staged" and path.parent.parent.name.endswith(".update"):
        shutil.rmtree(path.parent.parent, ignore_errors=True)
    (layout.shell / STAGED).unlink(missing_ok=True)


def forget_stale(layout: Layout, app: RunningApp | None) -> None:
    """Drop a staged App the running one already is (it was swapped in) or no longer replaces."""
    staged = read_staged(layout)
    if staged is None or app is None:
        return
    gone = not Path(str(staged.get("path") or "")).exists()
    if gone or not newer(staged.get("version"), app.version) or staged.get("app") != str(app.root):
        forget(layout)


def staged_for(layout: Layout, manifest: str) -> bool:
    staged = read_staged(layout)
    return bool(staged) and staged.get("manifest") == manifest and Path(str(staged.get("path") or "")).exists()


def record(layout: Layout, *, manifest: str, meta: dict, path: Path, app: RunningApp) -> None:
    value = {
        "manifest": manifest, "version": meta.get("version"), "platform": meta.get("platform"),
        "path": str(path), "app": str(app.root),
        "executable": "" if app.system == "linux" else meta.get("executable"),  # The AppImage is itself.
        "compat": meta.get("compat"), "python_minor": meta.get("python_minor"),
    }
    target = layout.shell / STAGED
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{STAGED}.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    replace_file(temporary, target)


def hosts(meta: dict, shell_compat: object, base: str) -> bool:
    """Whether the shell a layer's ``meta`` describes can host code of ``shell_compat`` on ``base``."""
    from openprogram.host._platform import runtime_layout as paths

    compat = meta.get("compat")
    if not isinstance(compat, dict) or not isinstance(shell_compat, int) \
            or not compat.get("min", shell_compat + 1) <= shell_compat <= compat.get("max", shell_compat - 1):
        return False
    minor = meta.get("python_minor")
    return not (isinstance(minor, str) and minor and minor != paths.base_minor(base))


# --- Checking an assembled App ------------------------------------------------------------


def _run(command: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"{what}: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise ReleaseError(f"{what}: {detail[-1] if detail else done.returncode}")
    return done


def authenticode(path: Path) -> tuple[str, str]:
    """The Authenticode status and signer subject of a Windows file."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
              "[pscustomobject]@{status=[string]$s.Status; subject=[string]$s.SignerCertificate.Subject} "
              "| ConvertTo-Json -Compress")
    system = os.environ.get("SystemRoot", r"C:\Windows")
    done = _run([str(Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
                 "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
                "the Authenticode signature could not be read")
    try:
        value = json.loads(done.stdout)
        return str(value.get("status") or ""), str(value.get("subject") or "")
    except (ValueError, AttributeError) as exc:
        raise ReleaseError(f"the Authenticode signature of {path.name} is unreadable") from exc


def verify(staged: Path, app: RunningApp, meta: dict) -> None:
    """Check an assembled App before it may be swapped in. Raises :class:`ReleaseError`.

    macOS: no extended attribute rides along (a clone of a running file may
    carry quarantine), ``codesign --verify --deep --strict`` passes, the team is
    the running App's and the version the layer's. Windows: the executable has
    the running App's Authenticode line: valid from the same signer, or plainly
    unsigned when the running App is. Linux: an executable ELF file. The signed
    pointer already vouches for every byte.
    """
    if app.system == "linux":
        try:
            with open(staged, "rb") as stream:
                elf = stream.read(4) == b"\x7fELF"
        except OSError:
            elf = False
        if not elf or not os.access(staged, os.X_OK):
            raise ReleaseError("the assembled AppImage is not an executable")
        return
    relative = str(meta.get("executable") or "")
    if not relative or not (staged / relative).is_file():
        raise ReleaseError("the assembled App has no executable")
    if app.system == "macos":
        _run(["/usr/bin/xattr", "-cr", str(staged)], "the assembled App's attributes could not be cleared")
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)],
             "the assembled App's signature is invalid")
        if mac_team(staged) != app.team:
            raise ReleaseError("the assembled App is signed by a different developer")
        if bundle_version(staged) != meta.get("version"):
            raise ReleaseError(f"the assembled App is {bundle_version(staged)}, not {meta.get('version')}")
        return
    status, subject = authenticode(staged / relative)
    have, signer = authenticode(app.root / "OpenProgram.exe")
    if have == "Valid":
        if status != "Valid" or subject != signer:
            raise ReleaseError("the assembled App is not signed by this App's publisher")
    elif status != "NotSigned":
        raise ReleaseError(f"the assembled App's signature is {status or 'unreadable'}")


__all__ = [
    "APPIMAGE", "RunningApp", "forget", "forget_stale", "hosts", "newer", "read_staged", "record", "running_app",
    "staged_for", "staged_target", "staging_root", "verify",
]
