"""Build a sealed version from one commit.

Nothing here touches what is running: the work happens in
``versions/.staging-<update>`` and, when the dependency set changed,
``bases/.staging-<id>``. A failure removes both and leaves the task's worktree.

A base is reused whenever the dependency set is the same, and the frontend
bundle is cloned from the parent unless a change can alter it, so most builds
cost a Python copy and a compile.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib

from openprogram.operations.self_update.delivery.activation import COMPONENTS, component_of

from . import layout as paths
from . import store
from .layout import Layout
from openprogram.host._platform import filesystem

GIB = 1024 ** 3
NEW_BASE_BYTES = 2 * GIB
VERSION_BYTES = 200 * 1024 ** 2
MARGIN_BYTES = 2 * GIB
TRIAL_COPY_CAP_BYTES = 4 * GIB
BASE_TIMEOUT = 3600
FRONTEND_TIMEOUT = 1800
DIRECTORY_BUSY_SECONDS = 30
STEP_TIMEOUT = 600
FRONTEND_COMPONENTS = frozenset({"web", "docs", "tui"})
DESTRUCTIVE_TRAILER = "Destructive-Migration"
# The generated reference pages read these; any other Python change leaves the
# bundle as it was. See scripts/docs_site/generate_reference.py.
REFERENCE_SOURCES = (
    "openprogram/interfaces/cli/",
    "openprogram/state/settings",
    "openprogram/models/providers/",
)
# Build scripts see these and nothing else from the caller, so a candidate's
# build cannot read the owner's API keys.
_PASSED_THROUGH = (
    "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "COMSPEC", "PATHEXT",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "UV_NATIVE_TLS",
    "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "OPENSSL_DIR", "OPENSSL_STATIC", "MACOSX_DEPLOYMENT_TARGET",
)


class BuildError(RuntimeError):
    """The build stopped; the message says why, and nothing was left behind."""


class NotEnoughSpace(BuildError):
    """This machine is short of disk, which says nothing about the commit."""


@dataclass(frozen=True)
class BuildRequest:
    update_id: str
    commit: str
    worktree: Path | None = None  # None builds in a temporary detached worktree.
    repository: Path | None = None  # Where to find the commit; defaults to the store's repo.
    parent: str | None = None  # The running version, when there is one.
    built_by: str = ""


@dataclass(frozen=True)
class BuildResult:
    version: str
    base: str
    new_base: bool
    frontend_rebuilt: bool
    components: tuple[str, ...]
    path: Path


def _base_environment(layout: Layout) -> dict[str, str]:
    home = layout.toolchain / "build-home"
    home.mkdir(parents=True, exist_ok=True)
    environment = {name: os.environ[name] for name in _PASSED_THROUGH if name in os.environ}
    # The build's own profile, all of it: Windows tools keep caches under
    # LOCALAPPDATA and APPDATA, and without them PowerShell writes its module
    # cache to Microsoft\Windows\PowerShell under the working directory, the
    # worktree, which the next build then refuses as uncommitted changes.
    # Only Windows reads the two; elsewhere they are inert.
    local_app_data, app_data = home / "AppData" / "Local", home / "AppData" / "Roaming"
    local_app_data.mkdir(parents=True, exist_ok=True)
    app_data.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(local_app_data),
        "APPDATA": str(app_data),
        "UV_CACHE_DIR": str(layout.toolchain / "cache" / "uv"),
        "npm_config_cache": str(layout.toolchain / "cache" / "npm"),
        "npm_config_update_notifier": "false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    })
    # A dependency with no wheel for this machine is compiled; rustup keeps its
    # toolchains under the real home, which the build's own HOME hides.
    rustup = Path.home() / ".rustup"
    if "RUSTUP_HOME" not in environment and rustup.is_dir():
        environment["RUSTUP_HOME"] = str(rustup)
    return environment


def _run(
    command: list[str], *, environment: dict[str, str], cwd: Path | None = None,
    timeout: float = STEP_TIMEOUT, what: str,
) -> None:
    try:
        result = subprocess.run(
            command, cwd=str(cwd) if cwd else None, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{what} took longer than {int(timeout)} s") from exc
    except OSError as exc:
        raise BuildError(f"{what} could not start: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()[-2000:]
        raise BuildError(f"{what} failed: {detail}")


def _git(arguments: list[str], cwd: Path, *, binary: bool = False) -> str | bytes:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *arguments],
            cwd=str(cwd), env=environment, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"git {arguments[0]} failed: {exc}") from exc
    if result.returncode != 0:
        raise BuildError(f"git {arguments[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def _clone_tree(source: Path, target: Path) -> None:
    """Copy a tree, as a copy-on-write clone where the filesystem has them."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = None
    if sys.platform == "darwin":
        command = ["cp", "-c", "-R", "-p", str(source), str(target)]
    elif sys.platform.startswith("linux"):
        command = ["cp", "--reflink=auto", "-R", "-p", str(source), str(target)]
    if command is not None and subprocess.run(command, capture_output=True, timeout=600).returncode == 0:
        return
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, symlinks=True)


def _trial_copy_bytes(state_dir: Path) -> int:
    """About what the trial's data copy will take, capped as the copy itself is."""
    total = 0
    for pattern in ("*.db", "*.sqlite3"):
        for path in state_dir.rglob(pattern):
            if "runtime" in path.relative_to(state_dir).parts:
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    from ..trial.data_copy import recent_conversations

    for chat in recent_conversations(state_dir / "projects"):
        total += sum(path.stat().st_size for path in chat.rglob("*") if path.is_file() and not path.is_symlink())
    return min(total, TRIAL_COPY_CAP_BYTES)


def required_space(layout: Layout, *, new_base: bool) -> int:
    trial = _trial_copy_bytes(layout.root.parent)
    return (NEW_BASE_BYTES if new_base else 0) + VERSION_BYTES + trial + MARGIN_BYTES


def check_space(layout: Layout, *, new_base: bool) -> None:
    required = required_space(layout, new_base=new_base)
    available = shutil.disk_usage(layout.root).free
    if available < required:
        raise NotEnoughSpace(
            f"not enough disk space: the update needs {required / GIB:.1f} GB and "
            f"{available / GIB:.1f} GB is free, {paths.disk_shortfall(required, available)} short"
        )


def changed_paths(worktree: Path, parent_commit: str | None, commit: str) -> list[str] | None:
    """Paths between the parent and the commit; ``None`` when there is no parent."""
    if parent_commit is None:
        return None
    output = str(_git(["diff", "--name-only", "--no-renames", "-z", f"{parent_commit}..{commit}"], worktree))
    return [path for path in output.split("\0") if path]


def components_of(paths_changed: list[str] | None) -> tuple[str, ...]:
    if paths_changed is None:
        return COMPONENTS
    found: set[str] = set()
    for path in paths_changed:
        component = component_of(path)
        if component == "full":
            return COMPONENTS
        if component is not None:
            found.add(component)
    return tuple(name for name in COMPONENTS if name in found)


def frontend_changes(paths_changed: list[str] | None) -> bool:
    """Whether this change can alter the Web, docs and terminal bundle."""
    if paths_changed is None:
        return True
    for path in paths_changed:
        component = component_of(path)
        if component == "full" or component in FRONTEND_COMPONENTS:
            return True
        if component == "python" and path.startswith(REFERENCE_SOURCES):
            return True
    return False


def _read_bytes(worktree: Path, relative: str) -> bytes:
    try:
        return (worktree / relative).read_bytes()
    except OSError as exc:
        raise BuildError(f"the commit has no {relative}: {exc}") from exc


def _base_id(worktree: Path) -> str:
    return paths.make_base_id(
        uv_lock=_read_bytes(worktree, "uv.lock"),
        pyproject=_read_bytes(worktree, "pyproject.toml"),
        product_runtime=_read_bytes(worktree, "scripts/release/product-runtime.json"),
    )


def _check_python_minor(layout: Layout, base_id: str) -> None:
    """The launcher embeds one Python minor; a candidate may not change it."""
    try:
        launcher_minor = (layout.root / "identity" / "python-minor").read_text(encoding="ascii").strip()
    except OSError:
        return
    if launcher_minor != paths.base_minor(base_id):
        raise BuildError(
            f"this change moves Python to {paths.base_minor(base_id)}, and the installed launcher "
            f"is built for {launcher_minor}; it needs a new signed release"
        )


def _build_base(
    layout: Layout, worktree: Path, staging: Path, clone_from: Path | None, environment: dict[str, str],
) -> None:
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(worktree / "scripts/release/build-base.ps1"), "-Dest", str(staging)]
        if clone_from is not None:
            command += ["-CloneFrom", str(clone_from)]
    else:
        command = ["bash", str(worktree / "scripts/release/build-base.sh"), "--dest", str(staging)]
        if clone_from is not None:
            command += ["--clone-from", str(clone_from)]
    _run(command, environment=environment, cwd=worktree, timeout=BASE_TIMEOUT, what="building the base")
    if not paths.base_python(staging).exists():
        raise BuildError("the base build produced no Python")


def _extract_archive(worktree: Path, commit: str, pathspecs: list[str], destination: Path) -> None:
    with tempfile.TemporaryFile() as buffer:
        try:
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "archive", "--format=tar", commit, "--", *pathspecs],
                cwd=str(worktree), stdout=buffer, stderr=subprocess.PIPE, timeout=120, check=True,
                env={"PATH": os.environ.get("PATH", os.defpath), "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_CONFIG_GLOBAL": os.devnull},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BuildError(f"git archive failed: {exc}") from exc
        buffer.seek(0)
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=buffer, mode="r:") as archive:
            archive.extractall(destination, filter="data")


def _write_dist_info(site: Path, worktree: Path, commit: str) -> str:
    """The distribution metadata ``importlib.metadata`` finds; returns the version."""
    project = tomllib.loads(_read_bytes(worktree, "pyproject.toml").decode("utf-8"))["project"]
    package_version = str(project["version"])
    info = site / f"openprogram-{package_version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: openprogram\nVersion: {package_version}\n", encoding="utf-8",
    )
    (info / "INSTALLER").write_text("openprogram-version-build\n", encoding="utf-8")
    (info / "top_level.txt").write_text("openprogram\n", encoding="utf-8")
    (info / "RECORD").write_text("", encoding="utf-8")
    scripts = project.get("scripts") or {}
    if scripts:
        entries = "".join(f"{name} = {target}\n" for name, target in scripts.items())
        (info / "entry_points.txt").write_text(f"[console_scripts]\n{entries}", encoding="utf-8")
    (site / "openprogram" / "_build_revision.txt").write_text(commit + "\n", encoding="ascii")
    return package_version


def toolchain_id(package_lock: bytes) -> str:
    """The id of a ``node_modules``, which every release's own version number leaves unchanged."""
    try:
        lock = json.loads(package_lock)
        lock.pop("version", None)
        for name, package in lock.get("packages", {}).items():
            if not name.startswith("node_modules/") and "/node_modules/" not in name:
                package.pop("version", None)  # The root and the workspace packages are ours.
    except (ValueError, AttributeError):
        return hashlib.sha256(package_lock).hexdigest()[:16]
    return hashlib.sha256(json.dumps(lock, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _drop_junctions(root: Path) -> None:
    """Remove the workspace links npm made on Windows.

    There they are junctions holding the absolute path of the build's own
    worktree, which is gone afterwards; nothing reads through them.
    """
    if os.name != "nt":
        return
    for current, directories, _names in os.walk(root):
        for name in list(directories):
            entry = os.path.join(current, name)
            if os.path.isjunction(entry):
                os.rmdir(entry)
                directories.remove(name)


def _ensure_toolchain(layout: Layout, worktree: Path, environment: dict[str, str]) -> Path:
    """``node_modules`` for this lock file, fetched the first time it is needed."""
    digest = toolchain_id(_read_bytes(worktree, "package-lock.json"))
    toolchain = layout.toolchain / digest
    if (toolchain / "node_modules").is_dir():
        return toolchain
    staging = layout.toolchain / f".staging-{digest}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    npm = shutil.which("npm", path=environment["PATH"])
    if npm is None:
        raise BuildError("npm is missing from the base")
    _run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], environment=environment,
         cwd=worktree, timeout=FRONTEND_TIMEOUT, what="fetching the Web dependencies (this needs network)")
    for current, directories, _names in os.walk(worktree):
        if ".git" in directories:
            directories.remove(".git")
        if "node_modules" in directories:
            directories.remove("node_modules")
            source = Path(current) / "node_modules"
            target = staging / source.relative_to(worktree)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
    if not (staging / "node_modules").is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        raise BuildError("npm ci produced no node_modules")
    _drop_junctions(staging)
    _move_directory(staging, toolchain)
    return toolchain


def _stage_frontend(
    layout: Layout, worktree: Path, target: Path, venv_python: Path, environment: dict[str, str],
) -> None:
    toolchain = _ensure_toolchain(layout, worktree, environment)
    # One bad docs page shows as an error page; it must not stop an update.
    variables = {
        **environment,
        "OPENPROGRAM_DOCS_PYTHON": str(venv_python),
        "OPENPROGRAM_DOCS_FORMAT": "quarantine",
    }
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(worktree / "scripts/release/stage-release-assets.ps1"), "-NodeModules", str(toolchain)]
    else:
        command = ["bash", str(worktree / "scripts/release/stage-release-assets.sh"), "--node-modules", str(toolchain)]
    _run(command, environment=variables, cwd=worktree, timeout=FRONTEND_TIMEOUT, what="building the Web and docs bundle")
    staged = paths.frontend_bundle(worktree)
    if not (staged / "index.html").is_file():
        raise BuildError("the frontend build produced no index.html")
    shutil.rmtree(target, ignore_errors=True)
    _clone_tree(staged, target)


def _copy_desktop(worktree: Path, commit: str, staging: Path) -> None:
    listed = str(_git(["ls-tree", "--name-only", commit, "apps/desktop/"], worktree)).splitlines()
    wanted = [name for name in listed if Path(name).name.startswith("main")]
    if not wanted:
        raise BuildError("the commit has no apps/desktop/main*")
    with tempfile.TemporaryDirectory(dir=staging) as scratch:
        _extract_archive(worktree, commit, wanted, Path(scratch))
        filesystem.replace_file(Path(scratch) / "apps" / "desktop", staging / "desktop")


def _read_shell_compat(layout: Layout, worktree: Path) -> object:
    """This commit's shell contract; refuse it if the installed shell cannot host it."""
    try:
        declared = json.loads((worktree / "apps/desktop/shell/compat.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise BuildError(f"apps/desktop/shell/compat.json is unreadable: {exc}") from exc
    value = declared.get("shell_compat")
    try:
        supported = json.loads((layout.root / "identity" / "shell-compat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return value  # No shell installed here.
    if not isinstance(value, int) or not supported["min"] <= value <= supported["max"]:
        raise BuildError("this change needs a new signed shell release")
    return value


def _move_directory(source: Path, target: Path) -> None:
    """Rename a finished directory into place.

    Windows refuses while a process that ran from it is still exiting; POSIX never retries.
    """
    deadline = time.monotonic() + DIRECTORY_BUSY_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def _repoint_venv(venv: Path, staged_base: Path, final_base: Path) -> None:
    """A venv names its base by absolute path; move it from staging to the real one."""
    config = venv / "pyvenv.cfg"
    config.write_text(config.read_text(encoding="utf-8").replace(str(staged_base), str(final_base)), encoding="utf-8")
    binary = venv / ("Scripts" if os.name == "nt" else "bin")
    for entry in binary.iterdir():
        if entry.is_symlink() and str(staged_base) in os.readlink(entry):
            target = os.readlink(entry).replace(str(staged_base), str(final_base))
            entry.unlink()
            entry.symlink_to(target)


def _settle_venv(venv: Path, staging: Path, final: Path) -> None:
    """Name the venv's final place. The shell activation scripts are never used."""
    config = venv / "pyvenv.cfg"
    config.write_text(config.read_text(encoding="utf-8").replace(str(staging), str(final)), encoding="utf-8")
    binary = venv / ("Scripts" if os.name == "nt" else "bin")
    for entry in binary.iterdir():
        if entry.name.lower().startswith("activate"):
            entry.unlink()


def _manifest_notes(worktree: Path, parent_commit: str | None, commit: str) -> dict:
    summary = str(_git(["log", "-1", "--format=%s", commit], worktree)).strip()
    revisions = f"{parent_commit}..{commit}" if parent_commit else f"{commit}^!" if _has_parent(worktree, commit) else commit
    trailers = str(_git(
        ["log", f"--format=%(trailers:key={DESTRUCTIVE_TRAILER},valueonly)", revisions], worktree,
    )).split()
    return {"summary": summary, "destructive_migration": " ".join(trailers) or None}


def _has_parent(worktree: Path, commit: str) -> bool:
    return " " in str(_git(["rev-list", "--parents", "-n", "1", commit], worktree)).strip()


def _check_worktree(worktree: Path, commit: str) -> None:
    head = str(_git(["rev-parse", "HEAD"], worktree)).strip()
    if head != commit:
        raise BuildError(f"the worktree is at {head[:12]}, not at {commit[:12]}")
    if str(_git(["status", "--porcelain"], worktree)).strip():
        raise BuildError("the worktree has uncommitted changes")


@contextmanager
def _worktree_for(layout: Layout, request: BuildRequest) -> Iterator[Path]:
    if request.worktree is not None:
        yield request.worktree
        return
    repository = request.repository or layout.repo
    path = layout.work / f".build-{paths.require_id(request.update_id, 'update id')}"
    shutil.rmtree(path, ignore_errors=True)
    layout.work.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "--detach", "--force", str(path), request.commit], repository)
    try:
        yield path
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=str(repository), capture_output=True, timeout=120,
        )
        shutil.rmtree(path, ignore_errors=True)


def seal_prebuilt(layout: Layout, staging: Path, manifest: dict, *, parent: str | None) -> Path:
    """Finish a version unpacked from a release, as if it had been built here.

    A venv names its base by absolute path, so a release does not carry one:
    ``staging`` holds everything else and the venv's interpreter links are made
    here by the same command a build uses. Sealing afterwards records this
    machine's paths and times.
    """
    base_id = str(manifest["base"])
    final = layout.version(paths.require_id(manifest["id"], "version id"))
    venv = staging / "venv"
    _run([str(paths.base_python(layout.base(base_id))), "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
         environment=_base_environment(layout), what="creating the virtual environment")
    _settle_venv(venv, staging, final)
    fields = {key: value for key, value in manifest.items() if key != "frontend_sha"}
    sealed = store.seal(layout, staging, {**fields, "parent": parent})
    differences = store.changed_files(sealed, base_id)
    if differences:
        raise BuildError(f"the unpacked version does not match its seal: {differences[0]}")
    return sealed


def build_version(layout: Layout, request: BuildRequest) -> BuildResult:
    """Build and seal ``request.commit``. Raises :class:`BuildError`."""
    staging = layout.staging(request.update_id)
    shutil.rmtree(staging, ignore_errors=True)
    layout.versions.mkdir(parents=True, exist_ok=True)
    layout.bases.mkdir(parents=True, exist_ok=True)
    with _worktree_for(layout, request) as worktree:
        _check_worktree(worktree, request.commit)
        base_id = _base_id(worktree)
        _check_python_minor(layout, base_id)
        staged_base = layout.base_staging(base_id)
        shutil.rmtree(staged_base, ignore_errors=True)
        try:
            return _build(layout, request, worktree, staging, base_id, staged_base)
        except BuildError:
            raise
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, store.SealError,
                tarfile.TarError, tomllib.TOMLDecodeError) as exc:
            raise BuildError(f"the build failed: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(staged_base, ignore_errors=True)


def _build(
    layout: Layout, request: BuildRequest, worktree: Path, staging: Path, base_id: str, staged_base: Path,
) -> BuildResult:
    parent = store.read_manifest(layout, request.parent) if request.parent else None
    parent_commit = str(parent["commit"]) if parent else None
    final_base = layout.base(base_id)
    new_base = not final_base.is_dir()
    check_space(layout, new_base=new_base)
    changed = changed_paths(worktree, parent_commit, request.commit)
    components = components_of(changed)
    environment = _base_environment(layout)

    # Tools come from the base; until one exists, from whatever built the caller.
    current_base = layout.base(str(parent["base"])) if parent else None
    tool_base = final_base if not new_base else current_base
    search = [str(tool_base / "bin")] if tool_base is not None and tool_base.is_dir() else []
    environment["PATH"] = os.pathsep.join([*search, os.environ.get("PATH", os.defpath)])
    environment["OPENPROGRAM_BUILD_PYTHON"] = sys.executable
    if new_base:
        _build_base(layout, worktree, staged_base, current_base if search else None, environment)
    using_base = staged_base if new_base else final_base

    venv = staging / "venv"
    staging.mkdir(parents=True)
    _run([str(paths.base_python(using_base)), "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
         environment=environment, what="creating the virtual environment")
    site = paths.site_packages(staging, base_id)
    site.mkdir(parents=True, exist_ok=True)
    _extract_archive(worktree, request.commit, ["openprogram", "pyproject.toml"], site)
    package_version = _write_dist_info(site, worktree, request.commit)
    venv_python = paths.venv_python(venv)
    environment["PATH"] = os.pathsep.join(
        [str(venv_python.parent), str(using_base / "bin"), os.environ.get("PATH", os.defpath)]
    )

    bundle = paths.frontend_bundle(site)
    previous_bundle = None
    if parent is not None:
        previous_bundle = paths.frontend_bundle(paths.site_packages(layout.version(str(parent["id"])), str(parent["base"])))
    rebuild = frontend_changes(changed) or previous_bundle is None or not previous_bundle.is_dir()
    if rebuild:
        _stage_frontend(layout, worktree, bundle, venv_python, environment)
    else:
        _clone_tree(previous_bundle, bundle)  # type: ignore[arg-type]

    _copy_desktop(worktree, request.commit, staging)
    shell_compat = _read_shell_compat(layout, worktree)

    if new_base:
        verifier = staged_base / "bin" / "verify-product-runtime.py"
        _run([str(venv_python), "-I", "-B", str(verifier), str(staged_base), "--probe-only",
              "--openprogram-version", package_version, "--tui", str(bundle / "tui" / "index.cjs")],
             environment=environment, cwd=staging, timeout=BASE_TIMEOUT, what="verifying the base")
        _move_directory(staged_base, final_base)
        _repoint_venv(venv, staged_base, final_base)

    built_at = datetime.now(timezone.utc)
    version_id = paths.make_version_id(request.commit, built_at)
    final = layout.version(version_id)
    _settle_venv(venv, staging, final)
    _run([str(venv_python), "-I", "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash",
          "-s", str(staging), "-p", str(final), str(site / "openprogram")],
         environment=environment, what="compiling the package")

    sealed = store.seal(layout, staging, {
        "id": version_id,
        "commit": request.commit,
        "base": base_id,
        "shell_compat": shell_compat,
        "built_by": request.built_by or f"openprogram {package_version}",
        "built_at": built_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "components": list(components),
        "parent": request.parent,
        "number": int((parent or {}).get("number") or 0) + 1,
        **_manifest_notes(worktree, parent_commit, request.commit),
    })
    differences = store.changed_files(sealed, base_id)
    if differences:
        raise BuildError(f"the sealed version does not match its seal: {differences[0]}")
    return BuildResult(version_id, base_id, new_base, rebuild, components, sealed)
