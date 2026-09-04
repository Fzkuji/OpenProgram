"""Freeze the installed controller runtime outside the replaceable App."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .supervisor import _tree_digest
from .store import SelfUpdateStore
from openprogram.store.session.git_session import atomic_write_text


@dataclass(frozen=True)
class ControllerBundle:
    python: Path
    installer_sha256: str
    runtime_sha256: str


def _installed_resources() -> Path:
    return Path("/Applications/OpenProgram.app/Contents/Resources")


def controller_environment() -> dict[str, str]:
    return {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def _copy_file(source, destination):
    shutil.copy2(source, destination)
    with open(destination, "rb") as handle:
        os.fsync(handle.fileno())
    return destination


def _runtime_python(runtime: Path) -> Path:
    manifest = runtime / "runtime-manifest.json"
    if runtime.is_symlink() or not runtime.is_dir() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("controller runtime must be a real directory with a regular manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("controller runtime manifest is invalid")
    relative = data.get("python")
    if data.get("schema") != 2 or not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("controller runtime manifest is invalid")
    python = (runtime / relative).resolve(strict=True)
    if not python.is_relative_to(runtime.resolve()) or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("controller Python is unavailable or escapes the runtime")
    return python


def _probe_runtime(runtime: Path, python: Path, module: str = "openprogram.self_update.supervisor") -> None:
    # This executes only copied trusted code, never the candidate. Isolated
    # Python excludes user-site/PYTHONPATH; the probe also rejects .pth escapes.
    code = (
        "from pathlib import Path; import sys,importlib; "
        "controller=importlib.import_module(sys.argv[2]); "
        "root=Path(sys.argv[1]).resolve(); "
        "assert Path(sys.prefix).resolve().is_relative_to(root); "
        "assert Path(sys.base_prefix).resolve().is_relative_to(root); "
        "assert Path(controller.__file__).resolve().is_relative_to(root); "
        "assert all(Path(p).resolve().is_relative_to(root) for p in sys.path); "
        "print('CONTROLLER_RUNTIME_OK')"
    )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code, str(runtime), module],
        cwd=str(runtime), env=controller_environment(), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or result.stdout.strip() != "CONTROLLER_RUNTIME_OK":
        raise ValueError("copied controller runtime cannot run independently; install a complete current App first")


def _load_bundle(target: Path) -> ControllerBundle:
    manifest = target / "manifest.json"
    installer = target / "install-app.sh"
    if (
        target.is_symlink() or not target.is_dir() or manifest.is_symlink()
        or not manifest.is_file() or installer.is_symlink() or not installer.is_file()
    ):
        raise ValueError("controller bundle is missing or contains a symlink")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict) or set(data) != {"schema", "runtime_sha256", "installer_sha256"}
        or type(data["schema"]) is not int or data["schema"] != 1
    ):
        raise ValueError("controller bundle manifest is invalid")
    runtime = target / "runtime"
    python = _runtime_python(runtime)
    if (
        _tree_digest(runtime) != data["runtime_sha256"]
        or hashlib.sha256(installer.read_bytes()).hexdigest() != data["installer_sha256"]
    ):
        raise ValueError("controller bundle digest mismatch")
    _probe_runtime(runtime, python)
    return ControllerBundle(python, data["installer_sha256"], data["runtime_sha256"])


def prepare_controller(update_dir: Path) -> ControllerBundle:
    """Publish once under the caller's existing update-store lock.

    Re-entry validates the saved bundle, not the installed App: it may already
    be a different version, or absent while the installer is recovering.
    """
    target = update_dir / "controller"
    if target.exists() or target.is_symlink():
        return _load_bundle(target)
    resources = _installed_resources()
    runtime = resources / "runtime"
    installer = resources / "update/install-app.sh"
    _runtime_python(runtime)
    if (
        resources.is_symlink() or installer.is_symlink() or not installer.is_file()
        or not installer.resolve().is_relative_to(resources.resolve())
    ):
        raise ValueError("trusted installed controller resources are unavailable")
    content = installer.read_bytes()
    content.decode("utf-8")
    digest = _tree_digest(runtime)
    with tempfile.TemporaryDirectory(prefix=".controller-", dir=update_dir) as temporary:
        staged = Path(temporary) / "controller"
        staged.mkdir(mode=0o700)
        shutil.copytree(runtime, staged / "runtime", symlinks=True, copy_function=_copy_file)
        for directory in (staged / "runtime").rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                SelfUpdateStore._fsync_directory(directory)
        SelfUpdateStore._fsync_directory(staged / "runtime")
        atomic_write_text(staged / "manifest.json", json.dumps({
            "schema": 1, "runtime_sha256": digest,
            "installer_sha256": hashlib.sha256(content).hexdigest(),
        }, sort_keys=True) + "\n")
        atomic_write_text(staged / "install-app.sh", content.decode("utf-8"))
        (staged / "install-app.sh").chmod(0o700)
        _load_bundle(staged)
        if _tree_digest(runtime) != digest or installer.read_bytes() != content:
            raise ValueError("installed controller resources changed during snapshot")
        os.replace(staged, target)
        SelfUpdateStore._fsync_directory(update_dir)
    # Paths used in the relocation probe refer to the staging directory.
    return ControllerBundle(_runtime_python(target / "runtime"), hashlib.sha256(content).hexdigest(), digest)


def prepare_build_inputs(update_dir: Path, candidate: Path, build_home: Path, *, deadline: float) -> dict[str, str]:
    """Copy matching installed dependencies and caches; never share writable inputs."""
    saved = update_dir / "controller"
    _load_bundle(saved)
    runtime = saved / "runtime"
    for cached, requested in (
        (runtime / "product-uv.lock", candidate / "uv.lock"),
        (runtime / "product-runtime.json", candidate / "scripts/release/product-runtime.json"),
    ):
        if cached.is_symlink() or requested.is_symlink() or cached.read_bytes() != requested.read_bytes():
            raise ValueError("candidate requires a different complete runtime dependency input")

    base = build_home / "runtime-base"
    copies = [(runtime, base)]
    for relative in (
        ".cache/uv",
        ".npm/_cacache",
        ".electron-gyp",
        "Library/Caches/electron",
        "Library/Caches/electron-builder",
    ):
        copies.append((Path.home() / relative, build_home / relative))
    for source, destination in copies:
        if source.is_symlink() or not source.is_dir() or source.stat().st_uid != os.getuid():
            raise ValueError("required local build cache is missing or not owner controlled")
        if destination.exists() or destination.is_symlink():
            raise ValueError("private build input already exists")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # APFS clones preserve isolation without duplicating large dependency caches.
        # Failure is explicit; never replace this with hard links or writable symlinks.
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("build input deadline expired")
        result = subprocess.run(
            ["/bin/cp", "-cR", str(source), str(destination)],
            env=controller_environment(), capture_output=True, timeout=remaining,
        )
        if result.returncode != 0:
            raise ValueError("could not create private copy-on-write build inputs")
        destination.chmod(0o700)
    return {
        "PATH": f"{base / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "OPENPROGRAM_SELF_UPDATE_RUNTIME_BASE": str(base),
        "OPENPROGRAM_UV_BIN": str(base / "bin/uv"),
        "OPENPROGRAM_BUILD_PYTHON": str(_runtime_python(base)),
        "UV_PYTHON_INSTALL_DIR": str(base / "python"),
        "UV_CACHE_DIR": str(build_home / ".cache/uv"),
        "UV_OFFLINE": "1",
        "NPM_CONFIG_OFFLINE": "true",
    }


@contextmanager
def build_inputs(update_dir: Path, candidate: Path, build_home: Path, *, deadline: float):
    """Release only this build's private dependency copies, including partial copies."""
    paths = [
        build_home / relative
        for relative in ("runtime-base", ".cache", ".npm", ".electron-gyp", "Library")
    ]
    if build_home.is_symlink() or any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError("private build input already exists")
    try:
        yield prepare_build_inputs(update_dir, candidate, build_home, deadline=deadline)
    finally:
        if build_home.is_symlink():
            raise ValueError("private build home changed during packaging")
        for path in paths:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
