"""Explicit + auto-discovered registry of @agentic_function modules.

Two mechanisms, in order:

  1. **AGENTIC_MODULES** — hand-maintained list of internal agentic
     module names (``openprogram/functions/agentics/<name>/``). Loaded
     explicitly so that import order and dependency conditions are
     obvious.

  2. **Auto-discovered external harnesses** — owner-recorded symlinks and
     directories under ``openprogram/functions/agentics/`` are treated as
     external harnesses. For each, we find its Python package
     (``<harness>/<pkg>/__init__.py``) and import ``<pkg>.agentics``.
     That sub-package must expose ``AGENTIC_FUNCTIONS = [...]`` — the
     ``@agentic_function`` decorators on the listed callables fire on
     import and register themselves with the shared AgentTool registry.

The auto-discovery convention replaces the old per-harness
``file_override`` mechanism: run ``openprogram programs install`` to record
the directory or development symlink; the harness's own
``<pkg>/agentics/__init__.py`` exports ``AGENTIC_FUNCTIONS``. No edit to this
file is required.

What's *exposed* to LLMs (Layer 2 of the selection cascade) is a
separate concern — a registered tool is exposed unless it opted out
with ``expose=False``, and ``exposed_names()`` in ``_runtime`` is the
live set. Membership in any registration mechanism here says "load
this module so its decorators run"; ``expose=False`` on the decorator
says "keep this one out of every LLM tool table".
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from typing import Iterator, Optional


logger = logging.getLogger(__name__)


AGENTIC_MODULES: list[str] = [
    # Framework primitive: ask_user is the in-execution "ask the human"
    # channel every agentic function can call — infrastructure, not a
    # user-facing app (the UI lists it under regular tools).
    "ask_user",
    # The goal decision agent — the single judgment entry called by
    # openprogram/agent/goal/ (the deterministic loop), runnable from the
    # Functions panel; the module also holds the goal-spec refiner.
    "goal",
    # Domain functions
    "extract_pdf_figures",
    "extract_pdf_tables",
    # Answers questions about OpenProgram itself by reading the product
    # documentation tree, with a read-only agent scoped to docs/.
    "docs_question",
    # 交互自测：串行走一遍 ask/confirm/form/ask_many 各形态（手动点 Run 体验）。
    "interaction_demo",
    # Task list workflow: plans a task into bounded items and runs them
    # serially, judging each with the goal decision agent above.
    "task_list",
]


# Names that should never be treated as external harnesses even if their
# directory looks like one (e.g. internal package private dirs).
_NOT_A_HARNESS = {
    "__pycache__", "pdf_layout",
}


def load_agentic_modules(agentics_dir: str) -> None:
    """Import every entry in AGENTIC_MODULES, then discover owner-recorded
    external harnesses under ``agentics_dir``.

    Failures are swallowed per-entry so a missing external harness
    symlink (e.g. on a fresh clone without the side repos) doesn't kill
    the whole import. Set ``OPENPROGRAM_DEBUG_REGISTRY=1`` to surface
    swallowed errors.
    """
    # 1. Internal explicit list
    for mod_name in AGENTIC_MODULES:
        try:
            importlib.import_module(
                f"openprogram.functions.agentics.{mod_name}"
            )
        except Exception as e:
            _debug_registry_error(mod_name, e)
            continue

    # 2. First-party *programs* — the agentic harnesses shipped as
    #    separate pip-installable packages (gui_harness / research_harness
    #    / wiki_agent_harness). Importing an installed package fires its
    #    @agentic_function decorator and self-registers the entry point.
    #    Absent packages are skipped silently — this is the supported way
    #    to ship gui_agent / research_agent / wiki_agent, replacing the
    #    old per-machine symlinks under agentics/. See functions/_programs.py.
    try:
        from openprogram.functions._programs import import_installed_programs
        import_installed_programs()
    except Exception as e:
        _debug_registry_error("programs", e)

    # 3. Auto-discovered external harnesses (local-dev symlinks in
    #    agentics_dir). Still supported for the
    #    ``<pkg>/agentics/__init__.py`` convention, but no longer the
    #    primary path — a developer working on a harness locally can just
    #    ``pip install -e`` their checkout and it registers via (2) above.
    for harness_name, harness_root in _iter_external_harness_dirs(agentics_dir):
        try:
            from openprogram.functions._programs import (
                owner_controlled_program_sources,
            )
            source = next(
                (row for row in owner_controlled_program_sources(agentics_dir)
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


# ---------------------------------------------------------------------------
# External harness auto-discovery
# ---------------------------------------------------------------------------


def _iter_external_harness_dirs(agentics_dir: str) -> Iterator[tuple[str, str]]:
    """Yield ``(name, real_path)`` for every entry under ``agentics_dir``
    that we treat as an external harness — i.e. a third-party agentic
    program dropped in here, whether as a **real directory** (the normal
    case: ``git clone`` into ``agentics/<name>/``) or a symlink (the
    local-dev case: ``ln -s`` your checkout).

    Accepting real directories is what makes "install a third-party
    harness = clone it into agentics/, done" work without symlinks
    (symlinks need admin/developer mode on Windows). A hyphenated name
    like ``Wiki-Agent-Harness`` is the canonical shape — Python can't
    import it directly, so the AGENTIC_MODULES loop ignores it, but this
    loop picks it up via its inner Python package (see
    :func:`_find_python_package`).

    Skips: dotfiles, the ``_NOT_A_HARNESS`` set (internal private dirs),
    plain ``.py`` files (single-module agentics, loaded elsewhere), and
    the official first-party programs — those are loaded explicitly by
    ``_programs.import_installed_programs`` (step 2 of
    :func:`load_agentic_modules`), so re-discovering their clone dirs
    here would import them a second time.
    """
    if not os.path.isdir(agentics_dir):
        return
    from openprogram.functions._programs import owner_controlled_program_sources
    skip = set(_NOT_A_HARNESS) | _official_program_dir_names()
    for row in sorted(
        owner_controlled_program_sources(agentics_dir),
        key=lambda item: os.path.basename(item["path"]),
    ):
        name = os.path.basename(row["path"])
        if name in skip or name.startswith("."):
            continue
        target = row["path"]
        if not os.path.isdir(target):
            continue
        yield name, target


def _official_program_dir_names() -> set[str]:
    """Clone-dir names of the first-party programs (GUI-Agent-Harness …),
    which ``_programs`` already imports — so auto-discovery skips them to
    avoid a double import. Best-effort: empty set if the catalogue can't
    be read."""
    try:
        from openprogram.functions._programs import KNOWN_PROGRAMS
        return {p.repo_dir_name for p in KNOWN_PROGRAMS}
    except Exception:
        return set()


def _find_python_package(harness_root: str) -> Optional[str]:
    """Locate the harness's agentic-exposing Python package directory.

    The convention is: the harness exposes itself via
    ``<pkg>/agentics/__init__.py``. We look for any ascii-identifier
    subdirectory under ``harness_root`` that contains both an
    ``__init__.py`` and an ``agentics/__init__.py``. That uniquely
    identifies the "main" package even when the harness root also
    contains vendored dependencies that happen to be Python packages
    (e.g. GUI harness ships ``desktop_env/`` alongside ``gui_harness/``).

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
    """Import ``<pkg>.agentics`` for the harness rooted at ``harness_root``.

    The ``AGENTIC_FUNCTIONS`` convention: that sub-package exports a list
    of decorated callables; we just import the module — the decorators
    on those callables fire on import and self-register into the shared
    AgentTool registry. We don't have to iterate ``AGENTIC_FUNCTIONS``
    ourselves; reading it is optional.
    """
    pkg_dir = _find_python_package(harness_root)
    if pkg_dir is None:
        return

    agentics_init = os.path.join(pkg_dir, "agentics", "__init__.py")
    if not os.path.isfile(agentics_init):
        return  # harness exists but doesn't expose any agentics — fine

    # Put the harness's package root on sys.path so its internal absolute
    # imports (e.g. ``from wiki_agent_harness.foo import bar``) resolve.
    sys_path_root = os.path.dirname(pkg_dir)
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)

    pkg_name = os.path.basename(pkg_dir)
    importlib.import_module(f"{pkg_name}.agentics")


# ---------------------------------------------------------------------------
# Single-file loader — the WebUI hot-reload path for external harness sources
# ---------------------------------------------------------------------------


def _load_external_file(
    agentics_dir: str, mod_name: str, rel_path: str
) -> None:
    """Import one file as ``agentics.<mod_name>``.

    Used by the WebUI function loader to re-execute an external
    harness's source file so an edit the user just made takes effect
    without a restart. The path must be owner-recorded.
    """
    abs_path = os.path.join(agentics_dir, rel_path)
    if not os.path.isfile(abs_path):
        return
    from openprogram.functions._programs import is_owner_controlled_program_path
    if not is_owner_controlled_program_path(abs_path):
        raise PermissionError(
            f"refusing to import unrecorded agentic source: {abs_path}"
        )

    inner_pkg_dir = os.path.dirname(abs_path)
    sys_path_root = os.path.dirname(inner_pkg_dir)
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)

    full_mod = f"openprogram.functions.agentics.{mod_name}"
    spec = importlib.util.spec_from_file_location(full_mod, abs_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_mod] = module
    spec.loader.exec_module(module)


# ---------------------------------------------------------------------------
# Enumeration for WebUI / CLI listing
# ---------------------------------------------------------------------------


def iter_agentic_files(agentics_dir: str) -> Iterator[tuple[str, str, bool]]:
    """Yield ``(module_name, file_path, is_harness)`` for every loadable
    agentic — used by the WebUI function browser and ``programs list``.

    - Internal entries: ``module_name`` is the AGENTIC_MODULES name;
      ``file_path`` is the on-disk ``.py``.
    - External harnesses: ``module_name`` is the harness's inner Python
      package name; ``file_path`` is its ``agentics/__init__.py``.

    Entries whose file is missing on this machine are silently skipped.
    """
    # Internal explicit list
    for mod_name in AGENTIC_MODULES:
        simple = os.path.join(agentics_dir, f"{mod_name}.py")
        pkg = os.path.join(agentics_dir, mod_name, "__init__.py")
        if os.path.isfile(simple):
            yield mod_name, simple, False
        elif os.path.isfile(pkg):
            yield mod_name, pkg, False

    # Auto-discovered external harnesses — yield the actual source file
    # of every function listed in AGENTIC_FUNCTIONS, so the WebUI scanner
    # (which parses `@agentic_function` decorators) can introspect them.
    import inspect as _inspect
    for _name, harness_root in _iter_external_harness_dirs(agentics_dir):
        pkg_dir = _find_python_package(harness_root)
        if pkg_dir is None:
            continue
        agentics_init = os.path.join(pkg_dir, "agentics", "__init__.py")
        if not os.path.isfile(agentics_init):
            continue
        # Make sure the harness package is importable, then read its
        # AGENTIC_FUNCTIONS export.
        sys_path_root = os.path.dirname(pkg_dir)
        if sys_path_root not in sys.path:
            sys.path.insert(0, sys_path_root)
        pkg_name = os.path.basename(pkg_dir)
        try:
            mod = importlib.import_module(f"{pkg_name}.agentics")
        except Exception as e:
            _debug_registry_error(f"iter:{pkg_name}", e)
            continue
        for fn in getattr(mod, "AGENTIC_FUNCTIONS", []) or []:
            # ``fn`` is the agentic_function wrapper object; the original
            # callable is stored under ``_fn``.
            inner = getattr(fn, "_fn", None) or fn
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


def _debug_registry_error(name: str, e: Exception) -> None:
    if os.environ.get("OPENPROGRAM_DEBUG_REGISTRY"):
        import traceback
        print(f"[registry] failed to load {name}: "
              f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Rescan — re-run discovery to pick up newly-installed harnesses at runtime
# ---------------------------------------------------------------------------


def _default_agentics_dir() -> Optional[str]:
    """The live ``functions/agentics/`` directory, or None if it can't be
    located. Used as :func:`rescan`'s default scan root."""
    try:
        from openprogram.functions import agentics as _ag
        return os.path.dirname(_ag.__file__)
    except Exception:
        try:
            from openprogram.functions._programs import agentics_dir
            return agentics_dir()
        except Exception:
            return None


def rescan(agentics_dir: Optional[str] = None) -> dict:
    """Re-run agentic discovery to pick up harnesses installed since boot.

    This is the single core both the manual "refresh" button and the
    background watcher call. It re-invokes :func:`load_agentic_modules`,
    which is idempotent — already-imported modules are skipped by Python's
    module cache, and a newly-present harness gets imported now, firing
    its ``@agentic_function`` decorators so they self-register into the
    shared tool registry. After this returns, the new functions are
    immediately live for the agent and visible to ``/api/functions``.

    Returns ``{"added": [tool_label, ...], "total": <count>}`` — ``added``
    lists tools that appeared this pass (the watcher / endpoint only
    broadcast when it's non-empty).

    Caveat (documented, intentional): only **additions** are reliable.
    Removing or hot-swapping a harness needs a worker restart — Python's
    module cache means an unimported / changed module isn't re-evaluated,
    and tearing down a live registry entry is unsafe. So ``rescan`` never
    *removes* tools; it only ever adds.
    """
    from openprogram.functions._runtime import all_tools
    scan_dir = agentics_dir or _default_agentics_dir()
    before = {t.label for t in all_tools()}
    if scan_dir:
        load_agentic_modules(scan_dir)
    after_tools = all_tools()
    after = {t.label for t in after_tools}
    return {"added": sorted(after - before), "total": len(after_tools)}
