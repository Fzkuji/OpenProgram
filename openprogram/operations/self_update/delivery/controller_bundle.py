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
import sys
import tempfile
import time

from ..control.supervisor import _tree_digest
from ..store import SelfUpdateStore
from openprogram.state.store.session.files.atomic_file import atomic_write_text
from openprogram.host._platform import filesystem
from openprogram.host._platform.architecture import interpreter_architecture


# Official release hashes (electron's checksums.json); a package.json bump must
# update this table, or the complete build refuses the candidate.
_ELECTRON_ARCHIVES = {
    ("45.2.1", "arm64"): (
        "electron-v45.2.1-darwin-arm64.zip",
        "a9a1a0a5b64df714957f83467c84823cb34792205922c6f136550b2403605922",
    ),
    ("45.2.1", "x64"): (
        "electron-v45.2.1-darwin-x64.zip",
        "308578d938129666a165f2a3b5c9e0fc866d553e6d53a38ca114bc5209b765f0",
    ),
    ("44.10.1", "arm64"): (
        "electron-v44.10.1-darwin-arm64.zip",
        "687f922034f1e991ab34ed7f47c4cb4e5c5e85e974b0ce155c22ce6d6f699aa3",
    ),
    ("44.10.1", "x64"): (
        "electron-v44.10.1-darwin-x64.zip",
        "d1c49452bd009411705d0d2ba140e126b6fdd2f69db837437327aab0b2476c2c",
    ),
    ("44.4.3", "arm64"): (
        "electron-v44.4.3-darwin-arm64.zip",
        "6b728f5dcfae74f3f936f2bca5b3cd9b9659ffea464f67939f004acb55425a85",
    ),
    ("44.4.3", "x64"): (
        "electron-v44.4.3-darwin-x64.zip",
        "015b52631d92187b552ff4e047255f596a7af4707e388a5890951f0b2645764e",
    ),
}
_ELECTRON_RELEASES = "https://github.com/electron/electron/releases/download"
ELECTRON_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024


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


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _electron_archive_spec(candidate: Path) -> tuple[str, str]:
    package = candidate / "apps/desktop/package.json"
    if package.is_symlink() or not package.is_file() or package.stat().st_size > 1_048_576:
        raise ValueError("candidate Electron package metadata is unavailable")
    data = json.loads(package.read_text(encoding="utf-8"))
    version = (data.get("devDependencies") or {}).get("electron")
    arch = {"arm64": "arm64", "x86_64": "x64"}[interpreter_architecture()]
    try:
        return _ELECTRON_ARCHIVES[(version, arch)]
    except KeyError:
        raise ValueError("candidate Electron archive is not pinned by the trusted controller") from None


def _download_electron_archive(cache: Path, filename: str, expected: str, *, deadline: float) -> Path:
    """Fetch the pinned official archive into the owner's cache; keep only verified bytes."""
    from openprogram.host.security import safe_http

    version = filename.split("-")[1]
    target = cache / filename
    remaining = deadline - time.time()
    if remaining <= 0:
        raise TimeoutError("build input deadline expired")
    with tempfile.TemporaryDirectory(prefix=".electron-download-", dir=cache) as name:
        partial = Path(name) / filename
        digest = hashlib.sha256()
        size = 0
        with safe_http.safe_client("self_update.electron", timeout=30,
                                   overall_timeout=remaining) as client:
            with client.stream("GET", f"{_ELECTRON_RELEASES}/{version}/{filename}") as response, \
                    partial.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > ELECTRON_ARCHIVE_MAX_BYTES:
                        raise ValueError("Electron download exceeds the expected size")
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Electron download does not match the pinned archive")
        filesystem.replace_file(partial, target)
    return target


def stage_electron_distribution(
    update_dir: Path,
    candidate: Path,
    *,
    deadline: float,
) -> Path:
    filename, expected = _electron_archive_spec(candidate)
    cache = Path.home() / "Library/Caches/electron"
    if not cache.exists() and not cache.is_symlink():
        cache.mkdir(mode=0o700, parents=True)
    if cache.is_symlink() or not cache.is_dir() or cache.stat().st_uid != os.getuid():
        raise ValueError("required local Electron archive cache is unavailable")
    matches = [
        path
        for path in cache.rglob(filename)
        if not path.is_symlink()
        and path.is_file()
        and path.stat().st_uid == os.getuid()
        and path.resolve().is_relative_to(cache.resolve())
        and _file_digest(path) == expected
    ]
    if not matches:
        matches = [_download_electron_archive(cache, filename, expected, deadline=deadline)]
    if len(matches) != 1:
        raise ValueError("one trusted cached Electron archive is required")
    target_dir = update_dir / "build-inputs"
    if target_dir.exists() or target_dir.is_symlink():
        raise ValueError("trusted build input already exists")
    target_dir.mkdir(mode=0o700)
    target = target_dir / filename
    remaining = deadline - time.time()
    if remaining <= 0:
        raise TimeoutError("build input deadline expired")
    result = subprocess.run(
        ["/bin/cp", "-c", str(matches[0]), str(target)],
        env=controller_environment(),
        capture_output=True,
        timeout=remaining,
    )
    if result.returncode != 0 or _file_digest(target) != expected:
        raise ValueError("could not stage the trusted Electron archive")
    target.chmod(0o400)
    return target


def _probe_runtime(runtime: Path, python: Path, module: str = "openprogram.operations.self_update.control.supervisor") -> None:
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
        filesystem.replace_file(staged, target)
        SelfUpdateStore._fsync_directory(update_dir)
    # Paths used in the relocation probe refer to the staging directory.
    return ControllerBundle(_runtime_python(target / "runtime"), hashlib.sha256(content).hexdigest(), digest)


def running_runtime() -> Path:
    """The runtime this worker was started from, else the installed App's.

    A runtime is a directory with a ``runtime-manifest.json``; a worker running
    from a source checkout has none, so the controller falls back to the App's.
    """
    for candidate in Path(sys.prefix).resolve().parents:
        if (candidate / "runtime-manifest.json").is_file():
            return candidate
    return _installed_resources() / "runtime"


def _private_python(runtime: Path) -> Path:
    """The interpreter a runtime's manifest names, which must be inside it."""
    data = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    relative = data.get("python")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("controller runtime manifest is invalid")
    python = (runtime / relative).resolve(strict=True)
    if not python.is_relative_to(runtime.resolve()) or not os.access(python, os.X_OK):
        raise ValueError("controller Python is unavailable or escapes the runtime")
    return python


def clone_private_runtime(update_dir: Path, runtime: Path,
                          module: str = "openprogram.operations.self_update.control.dev_stable") -> Path:
    """Copy a runtime into this update's directory and return its Python.

    Deploying and promoting replace the development runtime and the App's
    runtime while the controller runs, and this codebase imports lazily, so
    the controller runs from a copy that neither can touch. A clone costs
    nothing on APFS. Re-entry probes the saved copy.
    """
    target = update_dir / "controller" / "runtime"
    if not target.exists():
        if not (runtime / "runtime-manifest.json").is_file():
            raise ValueError("no installed runtime is available to run the controller from")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".controller-", dir=target.parent) as temporary:
            staged = Path(temporary) / "runtime"
            clone = ["/bin/cp", "-cR"] if sys.platform == "darwin" else ["cp", "-R", "--reflink=auto"]
            result = subprocess.run([*clone, str(runtime), str(staged)], capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise ValueError("could not copy the runtime for the controller: " + result.stderr.strip()[:500])
            filesystem.replace_file(staged, target)
    python = _private_python(target)
    _probe_runtime(target, python, module)
    return python


def prepare_build_inputs(update_dir: Path, candidate: Path, build_home: Path, *, deadline: float,
                         complete: bool = True) -> dict[str, str]:
    """Copy matching installed dependencies and caches; never share writable inputs.

    Only a ``complete`` package build runs electron-builder, so only it needs
    the pinned Electron archive. The node-gyp and electron-builder caches are
    copied when present and start empty otherwise.
    """
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
    copies = [(runtime, base, True)]
    for relative, required in (
        (".cache/uv", True),
        (".npm/_cacache", True),
        (".electron-gyp", False),
        ("Library/Caches/electron-builder", False),
    ):
        copies.append((Path.home() / relative, build_home / relative, required))
    for source, destination, required in copies:
        if destination.exists() or destination.is_symlink():
            raise ValueError("private build input already exists")
        if not required and not source.exists() and not source.is_symlink():
            destination.mkdir(mode=0o700, parents=True)
            continue
        if source.is_symlink() or not source.is_dir() or source.stat().st_uid != os.getuid():
            raise ValueError("required local build cache is missing or not owner controlled")
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
    environment = {
        "PATH": f"{base / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "OPENPROGRAM_SELF_UPDATE_RUNTIME_BASE": str(base),
        "OPENPROGRAM_UV_BIN": str(base / "bin/uv"),
        "OPENPROGRAM_BUILD_PYTHON": str(_runtime_python(base)),
        "UV_PYTHON_INSTALL_DIR": str(base / "python"),
        "UV_CACHE_DIR": str(build_home / ".cache/uv"),
        "UV_OFFLINE": "1",
        "NPM_CONFIG_OFFLINE": "true",
    }
    if complete:
        environment["OPENPROGRAM_SELF_UPDATE_ELECTRON_DIST"] = str(
            stage_electron_distribution(update_dir, candidate, deadline=deadline))
    return environment


@contextmanager
def build_inputs(update_dir: Path, candidate: Path, build_home: Path, *, deadline: float,
                 complete: bool = True):
    """Release only this build's private dependency copies, including partial copies."""
    paths = [
        build_home / relative
        for relative in ("runtime-base", ".cache", ".npm", ".electron-gyp", "Library")
    ]
    trusted_inputs = update_dir / "build-inputs"
    if (
        build_home.is_symlink()
        or trusted_inputs.exists()
        or trusted_inputs.is_symlink()
        or any(path.exists() or path.is_symlink() for path in paths)
    ):
        raise ValueError("private build input already exists")
    try:
        yield prepare_build_inputs(update_dir, candidate, build_home, deadline=deadline,
                                   complete=complete)
    finally:
        if build_home.is_symlink():
            raise ValueError("private build home changed during packaging")
        for path in paths:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        if trusted_inputs.is_symlink() or trusted_inputs.is_file():
            trusted_inputs.unlink()
        elif trusted_inputs.exists():
            shutil.rmtree(trusted_inputs)
