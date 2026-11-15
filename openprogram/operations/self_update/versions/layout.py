"""Where everything lives under ``<state>/runtime``, and whether we run from it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
BASE_ID = re.compile(r"py(\d+)\.(\d+)-[0-9a-f]{16}")


def valid_id(value: object) -> bool:
    """Whether ``value`` may name a version, base or update on disk."""
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def require_id(value: object, label: str = "id") -> str:
    if not valid_id(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return str(value)


def make_version_id(commit: str, when: datetime | None = None) -> str:
    """``<UTC yyyymmddThhmmssZ>-<12 hex of the commit>``."""
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"not a full commit id: {commit!r}")
    moment = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{moment:%Y%m%dT%H%M%SZ}-{commit[:12]}"


def python_minor(version: str) -> str:
    """``3.12`` from a pin such as ``3.12.10``."""
    parts = version.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"not a Python version: {version!r}")
    return f"{parts[0]}.{parts[1]}"


def base_minor(base: str) -> str:
    match = BASE_ID.fullmatch(base)
    if match is None:
        raise ValueError(f"not a base id: {base!r}")
    return f"{match[1]}.{match[2]}"


_OWN_LOCK_VERSION = re.compile(rb'(\[\[package\]\]\r?\nname = "openprogram"\r?\n)version = "[^"]*"\r?\n')


def make_base_id(*, uv_lock: bytes, pyproject: bytes, product_runtime: bytes) -> str:
    """``py<major.minor>-<16 hex>`` of everything that decides the dependency set.

    OpenProgram's own version is left out, in ``uv.lock`` as in ``pyproject``:
    every release changes it, and a base holds only the dependencies, so counting
    it would make every update download and unpack a whole new base.
    """
    uv_lock = _OWN_LOCK_VERSION.sub(rb"\1", uv_lock, count=1)
    project = tomllib.loads(pyproject.decode("utf-8")).get("project", {})
    dependencies = {
        "dependencies": project.get("dependencies", []),
        "optional-dependencies": project.get("optional-dependencies", {}),
        "requires-python": project.get("requires-python"),
    }
    digest = hashlib.sha256()
    for part in (uv_lock, json.dumps(dependencies, sort_keys=True).encode("utf-8"), product_runtime):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    minor = python_minor(json.loads(product_runtime)["python"])
    return f"py{minor}-{digest.hexdigest()[:16]}"


@dataclass(frozen=True)
class Layout:
    """The paths of one ``<root>``."""

    root: Path

    @property
    def pointer(self) -> Path:
        return self.root / "current"

    @property
    def previous_pointer(self) -> Path:
        return self.root / "current.prev"

    @property
    def lock(self) -> Path:
        return self.root / "current.lock"

    @property
    def pending_rollback(self) -> Path:
        return self.root / "pending-rollback"

    @property
    def developer(self) -> Path:
        return self.root / "developer.json"

    @property
    def seeding(self) -> Path:
        return self.root / ".seeding"

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def bases(self) -> Path:
        return self.root / "bases"

    @property
    def toolchain(self) -> Path:
        return self.root / "toolchain"

    @property
    def frontends(self) -> Path:
        return self.root / "pristine-frontend"

    @property
    def boot(self) -> Path:
        return self.root / "boot"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def trash(self) -> Path:
        return self.root / "trash"

    @property
    def recovery_log(self) -> Path:
        return self.root / "recovery.log"

    def version(self, version_id: str) -> Path:
        return self.versions / require_id(version_id, "version id")

    def staging(self, update_id: str) -> Path:
        return self.versions / f".staging-{require_id(update_id, 'update id')}"

    def base(self, base_id: str) -> Path:
        return self.bases / require_id(base_id, "base id")

    def base_staging(self, base_id: str) -> Path:
        return self.bases / f".staging-{require_id(base_id, 'base id')}"

    def unreplayable(self, release: str) -> Path:
        """Where the owner's commits go when an official release cannot carry them."""
        return self.root / "unreplayable" / require_id(release, "release")

    def attempts(self, version_id: str) -> Path:
        return self.boot / f"{require_id(version_id, 'version id')}.attempts"

    def migrating(self, version_id: str) -> Path:
        return self.boot / f"{require_id(version_id, 'version id')}.migrating"

    @property
    def trials(self) -> Path:
        return self.root / "trials"

    def launcher(self) -> Path:
        identity = self.root / "identity"
        if sys.platform == "darwin":
            return identity / "OpenProgram.app" / "Contents" / "MacOS" / "OpenProgram"
        return identity / ("openprogram-launcher.exe" if os.name == "nt" else "openprogram-launcher")


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def base_python(base: Path) -> Path:
    return base / ("python/python.exe" if os.name == "nt" else "python/bin/python3")


def site_packages(version_dir: Path, base_id: str) -> Path:
    if os.name == "nt":
        return version_dir / "venv" / "Lib" / "site-packages"
    return version_dir / "venv" / "lib" / f"python{base_minor(base_id)}" / "site-packages"


def frontend_bundle(site: Path) -> Path:
    return site / "openprogram" / "interfaces" / "server" / "_frontend"


def for_state(state_dir: Path | str) -> Layout:
    return Layout(Path(state_dir) / "runtime")


@dataclass(frozen=True)
class CurrentRuntime:
    """The sealed version this interpreter runs from."""

    layout: Layout
    version: str


def current_runtime(prefix: str | Path | None = None) -> CurrentRuntime | None:
    """Which version ``sys.prefix`` is, or ``None`` outside the version store.

    Identity comes from ``sys.prefix`` and never from the process image: on
    Windows the venv's redirector starts the base interpreter as a child, and
    the macOS launcher is a named helper outside the version.
    """
    venv = Path(sys.prefix if prefix is None else prefix)
    if venv.name != "venv" or venv.parent.parent.name != "versions":
        return None
    root = venv.parent.parent.parent
    if not valid_id(venv.parent.name) or not _has_pointer(root):
        return None
    return CurrentRuntime(Layout(root), venv.parent.name)


def _has_pointer(root: Path) -> bool:
    """A trial's sandbox hides the live store: macOS refuses the look, Linux mounts over it."""
    try:
        return (root / "current").is_file()
    except PermissionError:
        return False


def managed() -> bool:
    return current_runtime() is not None


def immutable_runtime() -> bool:
    """Whether this interpreter runs from an install the user does not edit.

    Sealed versions are recognised from where they run. An App's embedded
    runtime is told so by the variable its shell sets.
    """
    return os.environ.get("OPENPROGRAM_IMMUTABLE_RUNTIME") == "1" or managed()


def git_directory(base: Path) -> Path | None:
    """The Git a base ships, if it ships one."""
    git = base / "git"
    return git if git.is_dir() else None


def apply_base_environment(environ: dict[str, str] | None = None) -> None:
    """Point this process at the tools of the base it runs on.

    The base's Git goes first on ``PATH`` so the macOS ``/usr/bin/git`` shim
    cannot open a Command Line Tools dialog.
    """
    runtime = current_runtime()
    if runtime is None:
        return
    target = os.environ if environ is None else environ
    try:
        base_id = json.loads((Path(sys.prefix).parent / "version.json").read_text(encoding="utf-8"))["base"]
    except (OSError, ValueError, KeyError):
        return
    base = runtime.layout.base(str(base_id))
    browsers = base / "assets" / "playwright"
    if browsers.is_dir():
        target.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    entries = [str(base / "bin")] if (base / "bin").is_dir() else []
    git = git_directory(base)
    if git is not None:
        command_directory = git / ("cmd" if os.name == "nt" else "bin")
        entries.insert(0, str(command_directory))
        if os.name != "nt":
            target.setdefault("GIT_EXEC_PATH", str(git / "libexec" / "git-core"))
            target.setdefault("GIT_TEMPLATE_DIR", str(git / "share" / "git-core" / "templates"))
    if entries:
        existing = target.get("PATH", "")
        present = existing.split(os.pathsep)
        added = [entry for entry in entries if entry not in present]
        target["PATH"] = os.pathsep.join([*added, existing] if existing else added)


def disk_shortfall(required: int, available: int) -> str:
    """How much disk space is missing, in the unit that shows it.

    A need and a free amount within the same tenth of a gigabyte print as
    the same figure ("needs 5.0 GB and 5.0 GB is free"), so every disk-space
    message names the difference: megabytes under a gigabyte, gigabytes above.
    """
    missing = max(0, int(required) - int(available))
    if missing < 1024 ** 2:
        return "less than 1 MB"
    if missing < 1024 ** 3:
        return f"{missing / 1024 ** 2:.0f} MB"
    return f"{missing / 1024 ** 3:.1f} GB"

