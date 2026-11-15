"""Load built-in Programs, the user's Programs and owner-authorized harnesses.

Programs are plain Python packages: only their ``PROGRAM_ENTRIES`` register
as tools, and plain functions are not recorded. Owner-recorded harnesses
capture only their selected source roots;
Python dependencies and adjacent packages retain normal import behavior.

Built-in tools under ``programs/tools/`` register their ``@function``
leaves when ``openprogram.programs.tools`` is imported. A tool package that
also lists ``PROGRAM_ENTRIES`` (``TOOL_PROGRAMS``) registers those entries
here, through its own context.

Built-in applications live under ``programs/applications/<name>/``: a
Python package registers its ``PROGRAM_ENTRIES`` and runs its
``setup(ctx)``, if it has one; a directory with ``application.json`` (UI,
optional ``backend.py``) mounts it through ``_applications.loader.mount``,
the same path installed applications load through. One directory may
have both. A built-in application loads only while it is enabled
(``openprogram programs enable|disable <name>``, saved in the catalog);
disabling one disposes its context.

Every loaded package registers through its own :class:`ProgramContext`,
kept here by owner (``tool:<name>``, ``application:<name>``, the user
package's name, ``external:<package>``).

The user's Programs live in ``~/.openprogram/programs/`` (see
``openprogram.state.program_directory``): a Python package there loads through
``_packages``, an application directory through ``_applications.loader``
in the worker, and a harness clone recorded by ``openprogram programs
install`` through the external-harness path below. Unloading or reloading a package
disposes its context, which removes everything it registered.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from openprogram.programs._host.sources import use_dependency_directory

if TYPE_CHECKING:
    from openprogram.programs._host.context import ProgramContext


logger = logging.getLogger(__name__)

_contexts: dict[str, "ProgramContext"] = {}
_contexts_lock = threading.RLock()
_load_errors: list[dict[str, str]] = []


def program_context(owner: str, name: str, root: str = "") -> "ProgramContext":
    """The context of the package ``owner``, created on first use."""
    from openprogram.programs._host.context import ProgramContext
    with _contexts_lock:
        ctx = _contexts.get(owner)
        if ctx is None:
            ctx = _contexts[owner] = ProgramContext(name, root, owner=owner)
        return ctx


def dispose_program(owner: str) -> list[str]:
    """Undo everything the package ``owner`` registered. Returns the errors."""
    with _contexts_lock:
        ctx = _contexts.pop(owner, None)
    return ctx.dispose() if ctx is not None else []


def program_tools(owner: str) -> list[str]:
    """The tool names the package ``owner`` currently has registered."""
    with _contexts_lock:
        ctx = _contexts.get(owner)
        return list(ctx.contributions.get("tools", ())) if ctx is not None else []


# Tool packages whose ``PROGRAM_ENTRIES`` register through a context owned
# by ``tool:<name>``, by name and import path.
TOOL_PROGRAMS: dict[str, str] = {
    "goal": "openprogram.programs.tools.planning.goal",
}


def _builtin_applications_root() -> str:
    import openprogram.programs.applications as _applications
    return os.path.dirname(_applications.__file__)


def builtin_application_names() -> list[str]:
    """The built-in applications under ``programs/applications/``, by name.

    A directory is a built-in application when it holds an
    ``__init__.py`` or an ``application.json``; the harness checkouts
    beside them (their own ``pyproject.toml``, neither file at the top)
    load as installed programs instead.
    """
    root = _builtin_applications_root()
    return sorted(
        name for name in os.listdir(root)
        if name.isidentifier() and not name.startswith("_")
        and any(os.path.isfile(os.path.join(root, name, marker))
                for marker in ("__init__.py", "application.json"))
    )


def load_builtin_application(name: str) -> "ProgramContext":
    """Load ``programs/applications/<name>`` through its context.

    A Python package registers its ``PROGRAM_ENTRIES`` and runs ``setup``;
    an ``application.json`` mounts the UI, resource provider and
    operation entries exactly as for an installed application. The
    manifest id must be the directory name. Loading it again reuses its
    context and records nothing twice.
    """
    from pathlib import Path
    from openprogram.programs._host.context import call_setup
    root = os.path.join(_builtin_applications_root(), name)
    owner = f"application:{name}"
    with _contexts_lock:
        loaded = owner in _contexts
    ctx = program_context(owner, name, root)
    if os.path.isfile(os.path.join(root, "__init__.py")):
        module = importlib.import_module(f"openprogram.programs.applications.{name}")
        if getattr(module, "PROGRAM_ENTRIES", None):
            ctx.tools_from(module)
        setup = getattr(module, "setup", None)
        if callable(setup) and not loaded:
            call_setup(setup, ctx)
    if os.path.isfile(os.path.join(root, "application.json")) and "ui" not in ctx.contributions:
        from openprogram.programs._host.ui import catalog, loader
        if catalog.manifest(Path(root))["id"] != name:
            raise ValueError(f"application.json of {name} must have the id {name!r}")
        loader.mount(ctx, Path(root))
    return ctx


def builtin_application_enabled(name: str) -> bool:
    """Whether the built-in application ``name`` is enabled in the catalog."""
    from openprogram.programs._host.ui import catalog
    return catalog.builtin_enabled(name)


def builtin_application_states() -> list[dict]:
    """Every built-in application with its setting and what it registered.

    One row per name: ``id``, ``enabled`` (the catalog setting), ``loaded``
    (its context is mounted in this process) and ``entries`` (the tool
    names it registered), plus its ``title``, ``description`` and ``icon``
    (``_host.identity``). ``openprogram programs enable|disable`` and the
    Web switch change ``enabled`` through ``PATCH /api/applications/<id>``.
    """
    from openprogram.programs._host.identity import identity
    rows = []
    for name in builtin_application_names():
        owner = f"application:{name}"
        with _contexts_lock:
            loaded = owner in _contexts
        rows.append({"id": name, "enabled": builtin_application_enabled(name),
                     "loaded": loaded, "entries": sorted(program_tools(owner)),
                     **identity(Path(_builtin_applications_root()) / name)})
    return rows


def sync_builtin_application(name: str) -> Optional["ProgramContext"]:
    """Load or dispose ``programs/applications/<name>`` to match its setting.

    Disabling disposes the application's context: its entries, UI, resource
    provider, services and prompt sections go. Enabling mounts it again
    through a fresh context. Returns the context, or ``None`` when disabled.
    """
    if name not in builtin_application_names():
        raise KeyError(f"{name} is not a built-in application")
    if not builtin_application_enabled(name):
        dispose_program(f"application:{name}")
        return None
    return load_builtin_application(name)


# Names that should never be treated as external harnesses even if their
# directory looks like one (e.g. internal package private dirs).
_NOT_A_HARNESS = {
    "__pycache__", "workflow",
}


def builtin_program_modules() -> dict[str, str]:
    """Every built-in Program that is a Python package, by name.

    The ``TOOL_PROGRAMS`` and the built-in applications that are Python
    packages, each with its import path.
    """
    modules = dict(TOOL_PROGRAMS)
    root = _builtin_applications_root()
    for name in builtin_application_names():
        if os.path.isfile(os.path.join(root, name, "__init__.py")):
            modules[name] = f"openprogram.programs.applications.{name}"
    return modules


def load_program_modules(programs_dir: str | None = None) -> None:
    """Register the built-in Programs, the user's packages, then installed
    and owner-recorded external harnesses.

    Built-in and user Programs are plain Python packages; their
    ``PROGRAM_ENTRIES`` register as tools. Failures are swallowed per entry
    so one broken package doesn't kill the whole import. Set
    ``OPENPROGRAM_DEBUG_REGISTRY=1`` to add the traceback to the log. What
    failed in the latest pass is in :func:`load_errors`.
    """
    _load_errors.clear()
    # 1. Tool packages with PROGRAM_ENTRIES.
    for mod_name, module_path in TOOL_PROGRAMS.items():
        try:
            module = importlib.import_module(module_path)
            root = os.path.dirname(getattr(module, "__file__", "") or "")
            program_context(f"tool:{mod_name}", mod_name, root).tools_from(module)
        except Exception as e:
            _debug_registry_error(mod_name, e)
            continue

    # 1b. Built-in applications: plain packages under programs/applications/.
    try:
        names = builtin_application_names()
    except Exception as e:
        _debug_registry_error("applications", e)
        names = []
    for app_name in names:
        try:
            sync_builtin_application(app_name)
        except Exception as e:
            _debug_registry_error(f"application:{app_name}", e)

    # 2. The user's Python packages in ~/.openprogram/programs/.
    try:
        from openprogram.programs._host.packages import load_user_packages
        load_user_packages()
    except Exception as e:
        _debug_registry_error("user programs", e)

    # 3. Harnesses: owner-recorded directories (or local-dev symlinks) in
    #    the user's Programs directory, following the
    #    ``<pkg>/agentics/__init__.py`` convention.
    for harness_name, harness_root in _iter_external_harness_dirs(programs_dir):
        try:
            from openprogram.programs._host.sources import (
                owner_controlled_program_sources,
            )
            source = next(
                (row for row in owner_controlled_program_sources()
                 if row["path"] == harness_root),
                {},
            )
            logger.info(
                "loading owner-controlled agentic program path=%s source=%s",
                harness_root,
                source.get("source", "unknown"),
            )
            _import_external_harness(harness_root)
        except Exception as e:
            _debug_registry_error(f"external:{harness_name}", e)
            continue


def _iter_external_harness_dirs(programs_dir: str | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(name, real_path)`` for every recorded entry under the
    user's Programs directory (``programs_dir`` overrides it) that we treat
    as an external harness — i.e. a third-party agentic program installed
    there, whether as a **real directory** (the normal case: ``openprogram
    programs install`` clones it into ``programs/<name>/``) or a symlink
    (the local-dev case: ``ln -s`` your checkout).

    Accepting real directories is what makes "install a third-party
    harness = clone it, done" work without symlinks
    (symlinks need admin/developer mode on Windows). A hyphenated name
    like ``Some-Harness`` is the usual shape — Python can't import it
    directly, so the built-in loops ignore it, but this loop picks it up
    via its inner Python package (see :func:`_find_python_package`).

    Skips: dotfiles, the ``_NOT_A_HARNESS`` set (internal private dirs)
    and plain ``.py`` files (single-module agentics, loaded elsewhere).
    """
    from openprogram.state.program_directory import programs_root
    from openprogram.programs._host.sources import owner_controlled_program_sources
    skip = set(_NOT_A_HARNESS)
    base = programs_dir or str(programs_root())
    # Harness clones in the user's Programs directory, recorded at install.
    rows = owner_controlled_program_sources(base) if os.path.isdir(base) else []
    seen = set()
    for row in sorted(rows, key=lambda item: os.path.basename(item["path"])):
        if row["path"] in seen:
            continue
        seen.add(row["path"])
        name = os.path.basename(row["path"])
        if name in skip or name.startswith("."):
            continue
        target = row["path"]
        if not os.path.isdir(target):
            continue
        yield name, target


def _find_python_package(harness_root: str) -> Optional[str]:
    """Locate the harness's agentic-exposing Python package directory.

    The convention is: the harness exposes itself via
    ``<pkg>/agentics/__init__.py``. We look for any ascii-identifier
    subdirectory under ``harness_root`` that contains both an
    ``__init__.py`` and an ``agentics/__init__.py``. That uniquely
    identifies the "main" package even when the harness root also
    contains vendored dependencies that happen to be Python packages
    (e.g. a vendored ``third_party/`` package next to the harness's own).

    Falls back to the harness root itself when the harness IS the
    package and has agentics directly inside.
    """
    # case 1: harness root is itself a python package with agentics/
    if (os.path.isfile(os.path.join(harness_root, "__init__.py"))
            and os.path.isfile(os.path.join(
                harness_root, "agentics", "__init__.py"))):
        return harness_root

    # case 2: one of the children is the agentic-exposing package
    try:
        children = os.listdir(harness_root)
    except OSError:
        return None
    for child in sorted(children):
        if child.startswith((".", "_")):
            continue
        if not child.isidentifier():
            continue
        child_path = os.path.join(harness_root, child)
        if (os.path.isdir(child_path)
                and os.path.isfile(os.path.join(child_path, "__init__.py"))
                and os.path.isfile(os.path.join(
                    child_path, "agentics", "__init__.py"))):
            return child_path
    return None


def _import_external_harness(harness_root: str) -> None:
    """Capture the authorized package and register PROGRAM_ENTRIES exports."""

    pkg_dir = _find_python_package(harness_root)
    if pkg_dir is None:
        return

    agentics_init = os.path.join(pkg_dir, "agentics", "__init__.py")
    if not os.path.isfile(agentics_init):
        return  # harness exists but doesn't expose any agentics — fine

    # Put the harness's package root on sys.path so its internal absolute
    # imports (e.g. ``from some_harness.foo import bar``) resolve.
    sys_path_root = os.path.dirname(pkg_dir)
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)
    use_dependency_directory()

    pkg_name = os.path.basename(pkg_dir)
    from openprogram.programs._host.source_loader import install_program_source, register_public_entries
    from openprogram.programs._host.sources import is_owner_controlled_program_path
    install_program_source(pkg_name, pkg_dir,
                           lambda: is_owner_controlled_program_path(pkg_dir))
    module = importlib.import_module(f"{pkg_name}.agentics")
    names = register_public_entries(module, require_exports=True)
    program_context(f"external:{pkg_name}", pkg_name, pkg_dir)._adopt_tools(names)


# ---------------------------------------------------------------------------
# Single-file loader — the WebUI hot-reload path for external harness sources
# ---------------------------------------------------------------------------


def _load_external_file(
    agentics_dir: str, mod_name: str, rel_path: str
):
    """Import one file of an external harness and return its module.

    Used by the WebUI function loader to re-execute an external
    harness's source file so an edit the user just made takes effect
    without a restart. The path must be owner-recorded.
    """
    abs_path = os.path.join(agentics_dir, rel_path)
    if not os.path.isfile(abs_path):
        return None
    from openprogram.programs._host.sources import is_owner_controlled_program_path
    if not is_owner_controlled_program_path(abs_path):
        raise PermissionError(
            f"refusing to import unrecorded agentic source: {abs_path}"
        )

    inner_pkg_dir = os.path.dirname(abs_path)
    sys_path_root = os.path.dirname(inner_pkg_dir)
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)
    use_dependency_directory()

    slug = re.sub(r"[^A-Za-z0-9_]", "_", mod_name).strip("_") or "mod"
    digest = hashlib.sha256(os.path.realpath(abs_path).encode()).hexdigest()[:12]
    full_mod = f"openprogram.programs._external.{slug}_{digest}"
    from openprogram.programs._host.source_loader import ManagedSourceLoader, register_public_entries
    spec = importlib.util.spec_from_file_location(
        full_mod, abs_path, loader=ManagedSourceLoader(full_mod, abs_path),
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_mod] = module
    spec.loader.exec_module(module)
    register_public_entries(module, mod_name)
    return module


# ---------------------------------------------------------------------------
# Enumeration for WebUI / CLI listing
# ---------------------------------------------------------------------------


def iter_program_files(
    programs_dir: str | None = None,
) -> Iterator[tuple[str, str, bool]]:
    """Yield ``(module_name, file_path, is_harness)`` for every loadable
    Program — used by the WebUI function browser and ``programs list``.

    - Built-in entries: ``module_name`` is the name in
      :func:`builtin_program_modules`; ``file_path`` is the on-disk ``.py``
      that holds the entry (``<name>.py``, ``workflow.py`` or
      ``__init__.py`` of the package).
    - External harnesses: ``module_name`` is the harness's inner Python
      package name; ``file_path`` is its ``agentics/__init__.py``.

    Entries whose file is missing on this machine are silently skipped.
    """
    for mod_name, module_path in builtin_program_modules().items():
        spec = importlib.util.find_spec(module_path)
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin or not os.path.isfile(origin):
            continue
        pkg_dir = os.path.dirname(origin)
        named = os.path.join(pkg_dir, f"{mod_name}.py")
        workflow_py = os.path.join(pkg_dir, "workflow.py")
        if os.path.isfile(named):
            yield mod_name, named, False
        elif os.path.isfile(workflow_py):
            yield mod_name, workflow_py, False
        else:
            yield mod_name, origin, False

    # Auto-discovered external harnesses — yield the actual source file
    # of every function listed in PROGRAM_ENTRIES, so the WebUI scanner
    # can inspect the public source and method metadata.
    import inspect as _inspect
    for _name, harness_root in _iter_external_harness_dirs(programs_dir):
        pkg_dir = _find_python_package(harness_root)
        if pkg_dir is None:
            continue
        agentics_init = os.path.join(pkg_dir, "agentics", "__init__.py")
        if not os.path.isfile(agentics_init):
            continue
        # Make sure the harness package is importable, then read its
        # PROGRAM_ENTRIES export.
        sys_path_root = os.path.dirname(pkg_dir)
        if sys_path_root not in sys.path:
            sys.path.insert(0, sys_path_root)
        use_dependency_directory()
        pkg_name = os.path.basename(pkg_dir)
        try:
            mod = importlib.import_module(f"{pkg_name}.agentics")
        except Exception as e:
            _debug_registry_error(f"iter:{pkg_name}", e)
            continue
        for fn in getattr(mod, "PROGRAM_ENTRIES", []) or []:
            inner = _inspect.unwrap(fn)
            try:
                src_file = _inspect.getsourcefile(inner)
            except (TypeError, OSError):
                src_file = None
            name = getattr(fn, "__name__", None) or getattr(inner, "__name__", "")
            if src_file and os.path.isfile(src_file) and name:
                yield name, src_file, True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def load_errors() -> list[dict[str, str]]:
    """What could not load in the latest pass: ``{"name", "error"}`` rows."""
    return [dict(row) for row in list(_load_errors)]


def _debug_registry_error(name: str, e: Exception) -> None:
    # OPENPROGRAM_DEBUG_REGISTRY adds the traceback.
    _load_errors.append({"name": name, "error": f"{type(e).__name__}: {e}"})
    logger.warning("Cannot load Program %s: %s: %s", name, type(e).__name__, e,
                   exc_info=e if os.environ.get("OPENPROGRAM_DEBUG_REGISTRY") else None)


# ---------------------------------------------------------------------------
# Rescan — re-run discovery to pick up newly-installed harnesses at runtime
# ---------------------------------------------------------------------------


def rescan(programs_dir: Optional[str] = None) -> dict:
    """Re-run agentic discovery to pick up harnesses installed since boot.

    This is the single core both the manual "refresh" button and the
    background watcher call. It re-invokes :func:`load_program_modules`,
    which is idempotent — already-imported modules are skipped by Python's
    module cache. A newly-present harness is imported and its public entries
    register through the shared tool registry. The new functions are
    immediately live for the agent and visible to ``/api/functions``.

    Returns ``{"added": [tool_label, ...], "total": <count>}`` — ``added``
    lists tools that appeared this pass (the watcher / endpoint only
    broadcast when it's non-empty).

    Installed applications are the exception: their contexts are disposed
    and loaded again, so a changed, disabled or removed application leaves
    the registry here.

    Caveat (documented, intentional): for harnesses only **additions** are
    reliable. Removing or hot-swapping a harness needs a worker restart — Python's
    module cache means an unimported / changed module isn't re-evaluated,
    and tearing down a live registry entry is unsafe. So ``rescan`` never
    *removes* tools; it only ever adds.
    """
    from openprogram.programs._host.runtime import all_tools
    before = {t.label for t in all_tools()}
    load_program_modules(programs_dir)
    # Installed applications: in the worker, dispose and reload the ones
    # whose catalog entry or source changed.
    from openprogram.programs._host.ui import loader
    if loader.active():
        loader.sync()
    after_tools = all_tools()
    after = {t.label for t in after_tools}
    return {"added": sorted(after - before), "total": len(after_tools)}
