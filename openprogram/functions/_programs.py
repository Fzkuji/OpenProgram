"""First-party *programs* — the agentic harnesses that live as their own
git repositories and get installed **in-tree** under
``openprogram/functions/agentics/``.

The three flagship welcome-screen functions are big enough to keep their
own repos (own deps, tests, docs, release cadence):

    gui_agent       <- gui_harness          (GUI-Agent-Harness)
    research_agent  <- research_harness      (Research-Agent-Harness)
    wiki_agent      <- wiki_agent_harness    (Wiki-Agent-Harness)

Install model: clone into ``functions/agentics/``
-------------------------------------------------
Each program is ``git clone``-d into
``openprogram/functions/agentics/<Repo-Name>/`` as a **real directory**
(NOT a symlink, NOT a site-packages install). That keeps the harness
code right next to the bundled agentic functions:

  * it's discoverable by the same machinery that lists built-in functions,
  * it's editable in-place (the whole "agentic programming" pitch — a
    function is just an editable ``.py`` you can open in the UI), and
  * there are no per-machine absolute paths (the old approach committed
    symlinks pointing at the author's ``/Users/.../Documents/...`` which
    were dead on every other machine).

Each repo's top-level ``<package>/__init__`` imports its
``@agentic_function`` entry point, so simply *importing the package*
fires the decorator and self-registers the function into the shared
registry. :func:`import_installed_programs` puts each clone's directory
on ``sys.path`` and imports it at registry-load time; missing ones are
skipped silently.

Install / remove with::

    openprogram programs install gui      # git clone into functions/agentics/
    openprogram programs install all
    openprogram programs uninstall wiki

The clone directories are git-ignored by the parent repo (see
``.gitignore``) — they remain independent checkouts of their own repos.

Installing a THIRD-PARTY harness (any repo, not just these three) works
the same way without a registry edit: clone it into ``agentics/`` and it
auto-registers, as long as it satisfies the package contract. Full
procedure (the canonical install flow, written to be agent-executable):
``docs/installing-harnesses.md``.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Iterator, Optional


_GH = "https://github.com/Fzkuji"


def agentics_dir() -> Optional[str]:
    """Absolute path to ``openprogram/functions/agentics``.

    Computed from the top-level ``openprogram`` package so it works for
    both editable and site-packages installs, and *without* importing
    ``openprogram.functions.agentics`` (which would recurse — this module
    is imported during that package's load).
    """
    try:
        import openprogram
        return os.path.join(
            os.path.dirname(os.path.abspath(openprogram.__file__)),
            "functions", "agentics",
        )
    except Exception:
        return None


@dataclass(frozen=True)
class Program:
    """One in-tree agentic harness program.

    Attributes:
        function: The user-facing ``@agentic_function`` name the package
            registers (what the welcome screen / DEFAULT_TOOLS calls).
        package: The importable package name inside the repo (``import
            <package>``). Its ``__init__`` imports the entry point so the
            decorator self-registers on import.
        extra: The ``openprogram[<extra>]`` group carrying this program's
            heavy runtime deps (only populated for ``heavy`` programs).
        repo: HTTPS repo URL (also the ``git clone`` source).
        summary: One-line description for menus / install prompts.
        heavy: True when the program needs large / native deps (the GUI
            harness pulls torch via ultralytics + OpenCV). Used to warn
            before install and to keep it out of any "auto-install the
            light ones" default.
        public: False while the repo is not yet published. Kept in the
            catalogue so the program loads the moment it's present, but
            omitted from auto-install / git specs so a clone never fails
            on a private/missing repo.
        branch: Git ref to clone / pull.
    """

    function: str
    package: str
    extra: str
    repo: str
    summary: str
    heavy: bool = False
    public: bool = True
    branch: str = "main"

    @property
    def repo_dir_name(self) -> str:
        """Folder name the repo clones into (the URL's last segment)."""
        return self.repo.rstrip("/").split("/")[-1]

    def clone_dir(self, base: Optional[str] = None) -> Optional[str]:
        """Absolute path this program is (or would be) cloned to."""
        base = base or agentics_dir()
        return os.path.join(base, self.repo_dir_name) if base else None

    def in_tree_pkg_dir(self, base: Optional[str] = None) -> Optional[str]:
        """Path to the importable package inside an in-tree clone, or None.

        Returns ``<agentics>/<Repo-Name>/<package>`` when that directory
        exists with an ``__init__.py`` — i.e. the program is cloned in.
        """
        cd = self.clone_dir(base)
        if not cd:
            return None
        pkg = os.path.join(cd, self.package)
        return pkg if os.path.isfile(os.path.join(pkg, "__init__.py")) else None

    def git_url(self) -> str:
        """``git clone`` URL pinned to the branch is handled by the caller."""
        return f"{self.repo}.git"

    def is_installed(self) -> bool:
        """True when the program is available to import on this machine.

        Either it's cloned in-tree under ``functions/agentics/`` (the
        standard layout) or its package is importable some other way
        (e.g. ``pip install -e`` during local harness development).
        """
        if self.in_tree_pkg_dir():
            return True
        try:
            return importlib.util.find_spec(self.package) is not None
        except (ImportError, ValueError):
            return False


# The catalogue. Order is the welcome-screen / menu priority order.
KNOWN_PROGRAMS: list[Program] = [
    Program(
        function="gui_agent",
        package="gui_harness",
        extra="gui",
        repo=f"{_GH}/GUI-Agent-Harness",
        summary="Autonomous GUI agent — give it a task, it operates the desktop.",
        heavy=True,   # ultralytics -> torch, opencv-python, Pillow, pynput
        public=True,
    ),
    Program(
        function="research_agent",
        package="research_harness",
        extra="research",
        repo=f"{_GH}/Research-Agent-Harness",
        summary="Autonomous research agent — from topic to submission-ready paper.",
        heavy=False,  # only depends on openprogram itself
        public=True,
    ),
    Program(
        function="wiki_agent",
        package="wiki_agent_harness",
        extra="wiki",
        repo=f"{_GH}/Wiki-Agent-Harness",
        summary="Personal wiki agent — ingest sessions and organise a knowledge vault.",
        heavy=False,
        # NOTE: repo not yet public (404). Catalogued so it loads once
        # cloned in, but excluded from auto-install so nothing fails
        # trying to clone it.
        public=False,
    ),
]


# Convenience lookups -------------------------------------------------

_BY_FUNCTION = {p.function: p for p in KNOWN_PROGRAMS}
_BY_NAME: dict[str, Program] = {}
for _p in KNOWN_PROGRAMS:
    _BY_NAME[_p.function] = _p
    _BY_NAME[_p.extra] = _p
    _BY_NAME[_p.package] = _p
    _BY_NAME[_p.repo_dir_name] = _p
del _p


def iter_programs() -> Iterator[Program]:
    """Yield every catalogued program in priority order."""
    yield from KNOWN_PROGRAMS


def get_program(name: str) -> Optional[Program]:
    """Resolve a program by function / extra / package / repo-dir name."""
    return _BY_NAME.get(name)


def program_for_function(function: str) -> Optional[Program]:
    """Return the :class:`Program` that exposes ``function`` (or None)."""
    return _BY_FUNCTION.get(function)


def installed_programs() -> list[Program]:
    """Subset of :data:`KNOWN_PROGRAMS` available to import here."""
    return [p for p in KNOWN_PROGRAMS if p.is_installed()]


def program_function_names() -> set[str]:
    """Every function name the catalogue *could* expose (installed or not)."""
    return set(_BY_FUNCTION)


# Startup hook --------------------------------------------------------

def import_installed_programs() -> list[str]:
    """Import every installed program so its ``@agentic_function``
    decorators fire and self-register into the shared registry.

    For in-tree clones (the standard layout) each clone's own directory
    is put on ``sys.path`` first so ``import <package>`` resolves against
    ``functions/agentics/<Repo-Name>/<package>``. Programs that aren't
    present are skipped silently (the common case on a base checkout);
    set ``OPENPROGRAM_DEBUG_REGISTRY=1`` to surface import errors of a
    program that *is* present but fails to load.

    Returns the list of function names successfully registered.
    """
    base = agentics_dir()
    registered: list[str] = []
    for prog in KNOWN_PROGRAMS:
        # Make an in-tree clone importable by putting its repo dir (the
        # parent of the package) on sys.path.
        pkg_dir = prog.in_tree_pkg_dir(base)
        if pkg_dir:
            repo_dir = os.path.dirname(pkg_dir)
            if repo_dir not in sys.path:
                sys.path.insert(0, repo_dir)
        if not prog.is_installed():
            continue
        try:
            importlib.import_module(prog.package)
            registered.append(prog.function)
        except Exception as e:  # noqa: BLE001 — never let one break import
            if os.environ.get("OPENPROGRAM_DEBUG_REGISTRY"):
                import traceback
                print(f"[programs] failed to import {prog.package}: "
                      f"{type(e).__name__}: {e}")
                traceback.print_exc()
    return registered
