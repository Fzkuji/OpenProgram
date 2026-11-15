"""Program packages: loading, entry registration and load state.

A Program package is a Python package whose ``PROGRAM_ENTRIES`` lists the
functions exposed as tools. An optional ``ENTRY_OPTIONS`` dict maps an
entry name to tool options (timeout, toolset, ..., ``run_in_worker`` for a
tool that must run in the worker process instead of a forked subprocess,
and ``resumable`` for an entry whose ``continuation.step`` results survive
a restart). ``register_entries`` registers the entries of the built-in
Programs (tools and applications), which the registry imports at their own
paths, and of the user's packages.

User packages live in ``~/.openprogram/programs/<name>/`` and import under
their own top-level name. Each is its own Git repository. ``openprogram
programs check`` validates the package, runs its tests, commits and records
the commit in ``refs/openprogram/checked``. That ref is the only thing the
loader reads: a plain ``git commit`` is not loaded, and a working copy with
uncommitted edits keeps serving the checked version, across a worker restart
too.

The loader imports from ``<state>/programs-cache/<name>/<commit>/``, extracted
by ``git archive``, so an edit in the working directory cannot reach a lazy
import. A new version replaces the old one after the calls running in the
worker have finished, up to 30 seconds; past that the next pass tries again.
When the new version fails to import, the previous one is imported back from
the cache and the status reads ``error`` with a note naming both versions.
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from openprogram.host._platform.filesystem import replace_file

logger = logging.getLogger(__name__)

_IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
_lock = threading.RLock()

CHECKED_REF = "refs/openprogram/checked"
CACHE_DIRECTORY = "programs-cache"
GIT_MISSING = "Git is not available"
CALL_WAIT_SECONDS = 30.0
_CACHE_KEEP = 3
_GOOD_FILE = "good"
_MIGRATED_MARKER = ".checked-refs-migrated"
_COMMIT = re.compile(r"[0-9a-f]{40,64}")


@dataclass
class PackageStatus:
    """What the Programs page shows for one user package."""

    name: str
    path: str
    state: str = "pending"          # loaded | error | pending
    error: str = ""
    entries: list[str] = field(default_factory=list)
    revision: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "state": self.state,
            "error": self.error, "entries": list(self.entries),
            "revision": self.revision, "note": self.note,
        }


_status: dict[str, PackageStatus] = {}
_fingerprints: dict[str, tuple] = {}
# A checked commit that failed to import: not tried again while the version
# before it is serving, or every edit would dispose the working version.
_failed: dict[str, str] = {}
# Bumped when a replace is put off, so the watcher's next look sees a change.
_retries = 0
_calls: dict[str, int] = {}
_calls_changed = threading.Condition()


def _reset_calls() -> None:
    """A forked tool process starts with no calls and an unheld lock."""
    global _calls_changed
    _calls_changed = threading.Condition()
    _calls.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_calls)


def programs_root() -> Path:
    """``~/.openprogram/programs``, the user's program source directories."""
    from openprogram.runtime.agent.workflow_site import user_workflow_root
    return Path(user_workflow_root())


# ---------------------------------------------------------------------------
# Entry registration (built-in Programs and user packages)
# ---------------------------------------------------------------------------


@contextmanager
def _running_call(owner: str):
    """Count a call of ``owner`` in this process while it runs."""
    with _calls_changed:
        _calls[owner] = _calls.get(owner, 0) + 1
    try:
        yield
    finally:
        with _calls_changed:
            remaining = _calls.get(owner, 1) - 1
            if remaining > 0:
                _calls[owner] = remaining
            else:
                _calls.pop(owner, None)
                _calls_changed.notify_all()


def _wait_for_calls(owner: str, timeout: float | None = None) -> bool:
    """Whether the calls of ``owner`` finished within ``timeout`` seconds."""
    with _calls_changed:
        return _calls_changed.wait_for(lambda: not _calls.get(owner),
                                       CALL_WAIT_SECONDS if timeout is None else timeout)


def _bound_entry(entry, fn, owner: str = ""):
    """Call ``entry`` (decorators included) with ``fn`` bound as the running entry."""
    from openprogram.runtime.agent.workflow_site import bind_entry
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def bound(*args, **kwargs):
            with _running_call(owner), bind_entry(fn):
                return await entry(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def bound(*args, **kwargs):
            with _running_call(owner), bind_entry(fn):
                return entry(*args, **kwargs)
    return bound


def _revision(owner: str, fn) -> str:
    """The code identity a resumable entry is admitted and resumed under.

    A user package is its loaded Git commit. A built-in Program is the
    content of its package's Python files.
    """
    status = _status.get(owner)
    if status is not None and status.revision:
        return f"{owner}@{status.revision}"
    module = sys.modules.get(fn.__module__)
    package = Path(getattr(module, "__file__", "") or ".").resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" not in path.parts and "tests" not in path.relative_to(package).parts:
            digest.update(str(path.relative_to(package)).encode() + b"\0" + path.read_bytes())
    return f"{owner}#{digest.hexdigest()}"


def entry_problems(module) -> list[str]:
    """Contract problems in a Program module's ``PROGRAM_ENTRIES``."""
    entries = getattr(module, "PROGRAM_ENTRIES", None)
    if not isinstance(entries, (list, tuple)) or not entries:
        return ["PROGRAM_ENTRIES must be a non-empty list of functions."]
    problems = []
    options = getattr(module, "ENTRY_OPTIONS", {}) or {}
    if not isinstance(options, dict):
        problems.append("ENTRY_OPTIONS must be a dict keyed by entry name.")
    for entry in entries:
        fn = inspect.unwrap(entry)
        if not inspect.isfunction(fn):
            problems.append(f"{entry!r} in PROGRAM_ENTRIES is not a function.")
            continue
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            problems.append(f"{fn.__name__}: entries cannot be generators.")
        if not (inspect.getdoc(fn) or "").strip():
            problems.append(f"{fn.__name__}: an entry needs a docstring; it is the tool description.")
        entry_options = options.get(fn.__name__, {}) if isinstance(options, dict) else {}
        if entry_options.get("resumable"):
            if inspect.iscoroutinefunction(fn):
                problems.append(f"{fn.__name__}: a resumable entry must be synchronous.")
            if entry_options.get("run_in_worker"):
                problems.append(f"{fn.__name__}: a resumable entry cannot run in the worker.")
        for parameter in inspect.signature(fn).parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                problems.append(f"{fn.__name__}: *{parameter.name} cannot be a tool parameter.")
            elif parameter.annotation is inspect.Parameter.empty and parameter.name not in ("runtime",):
                problems.append(f"{fn.__name__}: parameter {parameter.name!r} needs a type annotation.")
    return problems


def register_entries(module, *, owner: str = "") -> list[str]:
    """Register ``PROGRAM_ENTRIES`` as tools. Returns the tool names."""
    from openprogram.runtime.agent.agent_method import register_agent_method, wrap_agent_method
    problems = entry_problems(module)
    if problems:
        raise ImportError("; ".join(problems))
    options_by_name = dict(getattr(module, "ENTRY_OPTIONS", {}) or {})
    from openprogram.programs._host.runtime import get
    taken = []
    for entry in module.PROGRAM_ENTRIES:
        fn = inspect.unwrap(entry)
        name = options_by_name.get(fn.__name__, {}).get("name") or fn.__name__
        existing = get(name)
        if existing is not None and getattr(existing, "_workflow_owner", None) != owner:
            taken.append(name)
    if taken:
        raise ImportError("Tool names already in use: " + ", ".join(taken))
    registered = []
    replacements = {}
    for entry in module.PROGRAM_ENTRIES:
        if getattr(entry, "_workflow_entry", False):
            tool = entry._agent_tool
            if get(tool.name) is not tool:
                # Registered before and disposed since (a disabled built-in
                # application enabled again): the same tool comes back.
                from openprogram.programs._host.runtime import Tier, register, tool_tier
                register(tool, toolsets=list(getattr(entry, "toolset", ()) or ()),
                         unsafe_in=list(getattr(entry, "unsafe_in", ()) or ()),
                         expose=tool_tier(tool) is not Tier.HIDDEN)
            registered.append(tool.name)
            continue
        fn = inspect.unwrap(entry)
        options = {"tool": True, "name": fn.__name__, **options_by_name.get(fn.__name__, {})}
        # Runs in the worker process instead of a forked subprocess, for
        # tools that use worker-owned registries.
        run_in_worker = bool(options.pop("run_in_worker", False))
        bound = _bound_entry(entry, fn, owner)
        if options.get("resumable"):
            bound._workflow_revision = functools.partial(_revision, owner, fn)
        wrapper = wrap_agent_method(bound, options)
        tool = register_agent_method(wrapper, options)
        wrapper._workflow_entry = True
        wrapper._workflow_owner = owner
        if tool is not None:
            tool._python_callable = wrapper
            tool._workflow_owner = owner
            if run_in_worker:
                tool._run_in_worker = True
            wrapper._agent_tool = tool
            registered.append(tool.name)
        replacements[id(entry)] = wrapper
        defining = sys.modules.get(fn.__module__)
        if defining is not None and getattr(defining, fn.__name__, None) in (entry, fn):
            setattr(defining, fn.__name__, wrapper)
    for name, value in list(vars(module).items()):
        if id(value) in replacements:
            setattr(module, name, replacements[id(value)])
    module.PROGRAM_ENTRIES = [replacements.get(id(entry), entry) for entry in module.PROGRAM_ENTRIES]
    return registered


# ---------------------------------------------------------------------------
# User packages
# ---------------------------------------------------------------------------


class _PackageFinder:
    """Resolve top-level names of loadable user packages."""

    def __init__(self):
        self.packages: dict[str, Path] = {}

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or "." in fullname:
            return None
        directory = self.packages.get(fullname)
        if directory is None:
            return None
        return importlib.util.spec_from_file_location(
            fullname, directory / "__init__.py",
            submodule_search_locations=[str(directory)],
        )


_finder = _PackageFinder()


def _install_finder() -> None:
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)


def _candidates(root: Path) -> dict[str, Path]:
    """The program directories under ``root`` that are Python packages.

    A working copy that lost its ``__init__.py`` still counts while it has a
    checked version: that is an uncommitted edit like any other.
    """
    from openprogram.state.program_directory import program_dirs
    found = {name: path for name, path in program_dirs(root).items()
             if (path / "__init__.py").is_file()}
    if root.is_dir():
        for child in root.iterdir():
            if (child.name not in found and not child.name.startswith((".", "_"))
                    and child.is_dir() and not child.is_symlink() and _checked_commit(child)):
                found[child.name] = child
    return dict(sorted(found.items()))


def _cache_root(root: Path) -> Path:
    """``<state>/programs-cache``, beside the ``programs`` directory ``root``."""
    return root.parent / CACHE_DIRECTORY


def working_copy_path(source: Path) -> Path:
    """Where a resolved file of a loaded version lives in the working copy.

    A file that is not in the version cache comes back unchanged.
    """
    root = programs_root()
    try:
        name, _commit, *rest = source.relative_to(_cache_root(root).resolve()).parts
    except ValueError:
        return source
    return root.resolve().joinpath(name, *rest)


def name_problem(name: str, directory: Path | None = None) -> str:
    """Why ``name`` cannot be a Program package name, or ``""``."""
    if not name.isidentifier() or name.startswith("_"):
        return f"{name!r} is not a valid Python package name."
    if name in sys.builtin_module_names or name in getattr(sys, "stdlib_module_names", ()):
        return f"{name!r} is a Python standard library module."
    base = directory.parent if directory is not None else programs_root()
    root = os.path.realpath(base)
    # A loaded user package comes from its checked copy in the cache.
    mine = (root + os.sep, os.path.realpath(_cache_root(base)) + os.sep)
    loaded = sys.modules.get(name)
    origin = getattr(loaded, "__file__", None) if loaded is not None else None
    if origin and not os.path.realpath(origin).startswith(mine):
        return f"{name!r} is already an imported module ({origin})."
    try:
        spec = importlib.machinery.PathFinder.find_spec(name)
    except (ImportError, ValueError):
        spec = None
    origin = getattr(spec, "origin", None) or (list(spec.submodule_search_locations or [""])[0] if spec else "")
    if spec is not None and origin and not os.path.realpath(origin).startswith(root + os.sep):
        return f"{name!r} is already an importable module ({origin})."
    return ""


def fingerprint(directory: Path) -> tuple:
    """A cheap signature of a package's files and Git state."""
    rows = []
    for base, names, files in os.walk(directory):
        names[:] = sorted(n for n in names if n not in _IGNORED_DIRS and not n.startswith("."))
        for file in sorted(files):
            if file.endswith((".pyc", ".pyo")):
                continue
            path = Path(base) / file
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append((str(path.relative_to(directory)), stat.st_mtime_ns, stat.st_size))
            if len(rows) > 2000:
                break
    git = directory / ".git"
    for name in ("HEAD", "index"):
        try:
            rows.append((f".git/{name}", (git / name).stat().st_mtime_ns, 0))
        except OSError:
            pass
    # A commit made within the file clock's granularity leaves every mtime
    # as it was; the commit the checkout points at tells it apart.
    rows.append((".git/commit", _git_head_commit(git), 0))
    rows.append((".git/checked", _read_ref(git, CHECKED_REF), 0))
    return tuple(rows)


def _git_head_commit(git: Path) -> str:
    """The commit ``HEAD`` names, from the files alone; ``""`` without one."""
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref: "):
        return head
    return _read_ref(git, head[len("ref: "):].strip())


def _read_ref(git: Path, ref: str) -> str:
    """The commit a ref names, from the files alone; ``""`` without one."""
    try:
        return (git / ref).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except OSError:
        pass
    return ""


def _checked_commit(directory: Path) -> str:
    """The commit ``openprogram programs check`` passed; ``""`` without one."""
    commit = _read_ref(directory / ".git", CHECKED_REF)
    return commit if _COMMIT.fullmatch(commit) else ""


def git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def record_checked(directory: Path, commit: str) -> None:
    """Record ``commit`` as the version of the package that may load."""
    completed = subprocess.run(["git", "-C", str(directory), "update-ref", CHECKED_REF, commit],
                               capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError("git update-ref failed: " + (completed.stderr or completed.stdout).strip())


def git_state(directory: Path) -> tuple[str, bool]:
    """``(HEAD commit, has uncommitted changes)``; ``("", True)`` without a commit."""
    if not (directory / ".git").exists():
        return "", True
    try:
        head = subprocess.run(["git", "-C", str(directory), "rev-parse", "--verify", "-q", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "", True
    # Importing the package rewrites __pycache__ in it; a package committed
    # without a .gitignore must not turn dirty just because it was loaded.
    changes = [line for line in status.splitlines() if line.strip() and not _generated(line)]
    return head, bool(changes)


def _generated(line: str) -> bool:
    """A ``git status --porcelain`` line for a cache file."""
    path = line[3:].split(" -> ")[-1].strip('"')
    return path.endswith((".pyc", ".pyo")) or bool(_IGNORED_DIRS.intersection(path.split("/")[:-1]))


def _evict(name: str) -> None:
    for module_name in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[module_name]
    importlib.invalidate_caches()


def _unregister(name: str) -> None:
    """Dispose the package's context: its tools leave the registry."""
    from openprogram.programs._host.registry import dispose_program
    dispose_program(name)


def _error_text(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    roots = (str(programs_root()), str(_cache_root(programs_root())))
    where = next((f"{frame.filename}:{frame.lineno}" for frame in reversed(frames)
                  if frame.filename.startswith(roots)), "")
    message = f"{type(exc).__name__}: {exc}"
    return f"{message} ({where})" if where else message


def _materialize(directory: Path, cache: Path, commit: str) -> Path:
    """The extracted copy of ``commit``, made once with ``git archive``."""
    target = cache / commit
    if (target / "__init__.py").is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f".{commit[:12]}-", dir=cache))
    try:
        archive = subprocess.run(["git", "-C", str(directory), "archive", "--format=tar", commit],
                                 capture_output=True, timeout=120)
        if archive.returncode != 0:
            raise RuntimeError("git archive failed: " + archive.stderr.decode(errors="replace").strip())
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(scratch, filter="data")
        if not (scratch / "__init__.py").is_file():
            raise RuntimeError(f"Commit {commit[:7]} has no __init__.py.")
        try:
            os.replace(scratch, target)
        except OSError:
            # Another process extracted the same commit first.
            if not (target / "__init__.py").is_file():
                raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def _last_good(cache: Path) -> str:
    """The commit that last imported cleanly, while its copy still exists."""
    try:
        commit = (cache / _GOOD_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return commit if _COMMIT.fullmatch(commit) and (cache / commit / "__init__.py").is_file() else ""


def _remember_good(cache: Path, commit: str) -> None:
    scratch = cache / f".{_GOOD_FILE}-{os.getpid()}-{threading.get_ident()}"
    scratch.write_text(commit, encoding="utf-8")
    replace_file(scratch, cache / _GOOD_FILE)


def _prune(cache: Path, keep: set[str]) -> None:
    """Drop old copies; a process that still imports from one is long replaced."""
    versions = [path for path in cache.iterdir() if path.is_dir() and _COMMIT.fullmatch(path.name)]
    versions.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in versions[_CACHE_KEEP:]:
        if path.name not in keep:
            shutil.rmtree(path, ignore_errors=True)


def _working_copy_note(directory: Path, checked: str, head: str, dirty: bool) -> str:
    if dirty:
        return "Uncommitted changes. The last checked version is still loaded."
    if head != checked:
        return f"Commit {head[:7]} is not checked. Run `openprogram programs check {directory}` to load it."
    return ""


def _import_version(name: str, version: Path) -> list[str]:
    """Replace whatever ``name`` serves with the copy at ``version``."""
    from openprogram.programs._host.registry import program_context
    _unregister(name)
    _evict(name)
    _finder.packages[name] = version
    try:
        module = importlib.import_module(name)
        return program_context(name, name, str(version)).tools_from(module)
    except BaseException:
        _unregister(name)
        _evict(name)
        raise


def _replace(name: str, directory: Path, status: PackageStatus, new: str, note: str, loaded: bool) -> None:
    """Serve ``new``, or keep the version before it when ``new`` fails."""
    global _retries
    cache = _cache_root(directory.parent) / name
    old = status.revision if loaded else _last_good(cache)
    if old == new:
        old = ""
    reason = ""
    target = None
    try:
        target = _materialize(directory, cache, new)
    except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
    if loaded and not reason and not _wait_for_calls(name):
        _fingerprints.pop(name, None)
        _retries += 1
        status.note = f"Version {new[:7]} is waiting for running calls to finish."
        logger.info("Program package %s: waiting for running calls before loading %s", name, new[:7])
        return
    tools: list[str] = []
    if target is not None:
        try:
            tools = _import_version(name, target)
        except BaseException as exc:  # a broken package must not stop the others
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            reason = _error_text(exc)
    if not reason:
        status.state, status.error, status.revision, status.note = "loaded", "", new, note
        status.entries = list(tools)
        _failed.pop(name, None)
        _remember_good(cache, new)
        _prune(cache, {new, old})
        return
    logger.warning("Cannot load Program package %s at %s: %s", name, new[:7], reason)
    _failed[name] = new
    if old and target is None and loaded:
        # Nothing was disposed: the old version is still in place.
        status.state, status.error = "error", reason
        status.note = f"Kept version {old[:7]}; version {new[:7]} failed."
        return
    if old:
        try:
            tools = _import_version(name, cache / old)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning("Cannot load Program package %s back at %s: %s", name, old[:7], _error_text(exc))
        else:
            status.state, status.error, status.revision = "error", reason, old
            status.note = f"Kept version {old[:7]}; version {new[:7]} failed."
            status.entries = list(tools)
            return
    status.state, status.error, status.revision, status.entries, status.note = "error", reason, "", [], ""


def _load_one(name: str, directory: Path) -> None:
    status = _status.setdefault(name, PackageStatus(name, str(directory)))
    status.path = str(directory)
    problem = name_problem(name, directory)
    if problem:
        _unregister(name)
        status.state, status.error, status.entries, status.note = "error", problem, [], ""
        status.revision = ""
        return
    from openprogram.programs._host.registry import program_tools
    loaded = bool(program_tools(name))
    if not git_available():
        if loaded:
            status.note = GIT_MISSING
        else:
            status.state, status.error, status.entries, status.note = "error", GIT_MISSING, [], ""
        return
    checked = _checked_commit(directory)
    if not checked:
        note = f"Run `openprogram programs check {directory}` to make it callable."
        if loaded:
            status.note = note
        else:
            status.state, status.error, status.note = "pending", "", note
        return
    if loaded and _failed.get(name) == checked:
        return
    head, dirty = git_state(directory)
    note = _working_copy_note(directory, checked, head, dirty)
    if loaded and status.revision == checked and status.state == "loaded":
        status.note = note
        return
    _replace(name, directory, status, checked, note, loaded)


def _migrate_checked_refs(root: Path, found: dict[str, Path]) -> None:
    """Once per directory: a clean package with a commit counts as checked.

    The packages the loader served before it read the checked ref keep
    loading; a commit made after this needs the check like any other.
    """
    marker = root / _MIGRATED_MARKER
    if marker.exists() or not root.is_dir() or not git_available():
        return
    for directory in found.values():
        if _checked_commit(directory):
            continue
        head, dirty = git_state(directory)
        if head and not dirty:
            try:
                record_checked(directory, head)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                logger.warning("Cannot mark %s as checked: %s", directory, exc)
    marker.write_text("", encoding="utf-8")


def _point_finder(name: str, directory: Path) -> None:
    """Let packages import ``name`` by its serving or last good version."""
    from openprogram.programs._host.registry import program_tools
    cache = _cache_root(directory.parent) / name
    status = _status.get(name)
    version = status.revision if status is not None and status.revision and program_tools(name) else ""
    version = version or _last_good(cache) or _checked_commit(directory)
    if not version:
        _finder.packages.pop(name, None)
        return
    try:
        _finder.packages[name] = _materialize(directory, cache, version)
    except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        _finder.packages.pop(name, None)
        logger.warning("Cannot extract Program package %s at %s: %s", name, version[:7], exc)


def load_user_packages(root: Path | None = None) -> dict[str, PackageStatus]:
    """Load, reload or drop the user's packages to match the directory."""
    from openprogram.runtime.agent.workflow_site import add_workflow_root
    root = root or programs_root()
    with _lock:
        add_workflow_root(root)
        add_workflow_root(_cache_root(root))
        _install_finder()
        found = _candidates(root)
        _migrate_checked_refs(root, found)
        for name in list(_status):
            if name not in found:
                _unregister(name)
                _evict(name)
                _finder.packages.pop(name, None)
                _status.pop(name, None)
                _fingerprints.pop(name, None)
                _failed.pop(name, None)
                shutil.rmtree(_cache_root(root) / name, ignore_errors=True)
        for name, directory in found.items():
            if not name_problem(name, directory):
                _point_finder(name, directory)
        for name, directory in found.items():
            current = fingerprint(directory)
            if _fingerprints.get(name) == current and name in _status:
                continue
            _fingerprints[name] = current
            _load_one(name, directory)
        return dict(_status)


def package_statuses() -> list[dict]:
    # Without ``_lock``: a load pass holds it while running calls finish.
    return [status.to_dict() for status in sorted(list(_status.values()), key=lambda s: s.name)]


def root_fingerprint() -> tuple:
    root = programs_root()
    rows = [(name, fingerprint(directory)) for name, directory in _candidates(root).items()]
    return (*rows, ("", _retries))


__all__ = [
    "CHECKED_REF", "PackageStatus", "entry_problems", "fingerprint", "git_available", "git_state",
    "load_user_packages", "name_problem", "package_statuses", "programs_root",
    "record_checked", "register_entries", "root_fingerprint", "working_copy_path",
]
