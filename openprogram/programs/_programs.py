"""First-party *programs* — the agentic harnesses that live as their own
git repositories and get installed **in-tree** under
``openprogram/programs/applications/``.

The three flagship welcome-screen functions are big enough to keep their
own repos (own deps, tests, docs, release cadence):

    gui_agent       <- gui_harness          (GUI-Agent-Harness)
    research_agent  <- research_harness      (Research-Agent-Harness)
    wiki_agent      <- wiki_agent_harness    (Wiki-Agent-Harness)

Install model: owner-recorded source under ``programs/applications/``
------------------------------------------------------------------
The standard installer clones each program into
``openprogram/programs/applications/<Repo-Name>/`` as a **real directory**
(not a site-packages install). An existing development symlink can be
recorded explicitly without modifying its target. This keeps the harness
code right next to the bundled agentic functions:

  * it's discoverable by the same machinery that lists built-in functions,
  * it's editable in-place (the whole "agentic programming" pitch — a
    function is just an editable ``.py`` you can open in the UI), and
  * there are no per-machine absolute paths (the old approach committed
    symlinks pointing at the author's ``/Users/.../Documents/...`` which
    were dead on every other machine).

The registration contract is the ``agentics`` SUB-package, not the
top-level package: :func:`import_installed_programs` puts each clone's
directory on ``sys.path`` and imports ``<package>.agentics`` at
registry-load time — importing it fires the ``@agentic_function``
decorators, which self-register into the shared registry. The top-level
``<package>/__init__`` is deliberately NOT the entry point and must stay
dependency-light: discovery imports it (as the parent) on every startup,
including on machines without the harness's optional deps. Missing
programs are skipped silently. (Same contract as third-party harnesses —
see ``docs/installing-harnesses.md``.)

Install / remove with::

    openprogram programs install gui      # git clone into programs/applications/
    openprogram programs install all
    openprogram programs install https://github.com/owner/Some-Harness
    openprogram programs uninstall wiki
    openprogram programs uninstall Some-Harness

The clone directories are git-ignored by the parent repo (see
``.gitignore``) — they remain independent checkouts of their own repos.

Installing a THIRD-PARTY harness (any repo, not just these three) uses
the same CLI command. The installer verifies the package contract and records
the approved source; unrecorded directories are not imported. Full
procedure (the canonical install flow, written to be agent-executable):
``docs/installing-harnesses.md``.
"""

from __future__ import annotations

import configparser
import importlib
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


_GH = "https://github.com/Fzkuji"
_PROGRAM_SOURCES_FILE = "program-sources.json"
_sources_lock = threading.Lock()  # ponytail: one lock for the sources RMW; split if writers contend


def _canonical_repo_url(value: str) -> str:
    return str(value or "").strip().rstrip("/").removesuffix(".git")


def _catalogued_clone_origin(root: str, expected: str) -> str | None:
    config_path = Path(root) / ".git" / "config"
    parser = configparser.RawConfigParser()
    try:
        with config_path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
        origin = parser.get('remote "origin"', "url")
    except (OSError, configparser.Error):
        return None
    return origin if _canonical_repo_url(origin) == _canonical_repo_url(expected) else None


def _program_sources_path() -> Path:
    from openprogram.protected_paths import program_sources_path
    return Path(program_sources_path())


def _read_program_sources() -> list[dict]:
    try:
        payload = json.loads(_program_sources_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("programs", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _recorded_root(row: dict) -> str | None:
    raw = str(row.get("path", "")).strip()
    if not raw:
        return None
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        return None
    return os.path.abspath(expanded)


def _write_program_sources(rows: list[dict]) -> None:
    target = _program_sources_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": 1, "programs": rows}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _update_program_sources(mutate) -> None:
    from openprogram.auth.credentials import _private_file_lock
    from openprogram.paths import get_state_dir

    target = _program_sources_path()
    with _sources_lock:
        with _private_file_lock(target, root=get_state_dir()):
            mutate(_read_program_sources())


def _is_direct_child(path: str, base: str) -> bool:
    # Check where the install entry lives, not where a deliberate dev
    # symlink points.  The owner records the entry under applications explicitly.
    parent = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    return parent.casefold() == os.path.realpath(base).casefold()


def record_program_source(path, *, source: str, kind: str = "git") -> None:
    """Record an owner-installed harness before runtime import is allowed."""
    raw = os.fspath(path).strip()
    if not raw:
        raise ValueError("program source path must not be empty")
    base = applications_dir()
    root = os.path.abspath(os.path.expanduser(raw))
    if not base or not _is_direct_child(root, base) or not os.path.isdir(root):
        raise ValueError("program source must be a directory directly under applications")

    def _mutate(rows: list[dict]) -> None:
        kept = [
            row for row in rows
            if (existing := _recorded_root(row)) is None
            or existing.casefold() != root.casefold()
        ]
        kept.append({
            "path": root,
            "source": str(source),
            "kind": str(kind),
            "recorded_at": time.time(),
        })
        _write_program_sources(kept)

    _update_program_sources(_mutate)


def remove_program_source(path) -> None:
    raw = os.fspath(path).strip()
    if not raw:
        raise ValueError("program source path must not be empty")
    root = os.path.abspath(os.path.expanduser(raw))

    def _mutate(rows: list[dict]) -> None:
        target = _program_sources_path()
        if not target.exists():
            return
        kept = [
            row for row in rows
            if (existing := _recorded_root(row)) is None
            or existing.casefold() != root.casefold()
        ]
        _write_program_sources(kept)

    _update_program_sources(_mutate)


def owner_controlled_program_sources(base: str | None = None) -> list[dict]:
    """Return valid owner-recorded roots, optionally limited to one directory."""
    out = []
    for row in _read_program_sources():
        root = _recorded_root(row)
        if root is None:
            continue
        if os.path.isdir(root) and (base is None or _is_direct_child(root, base)):
            out.append({**row, "path": root})
    return out


def owner_programs_roots() -> list[Path]:
    """Return source ``openprogram/programs`` roots recorded by the owner."""
    roots: dict[str, Path] = {}
    for row in owner_controlled_program_sources():
        application = Path(row["path"]).resolve()
        applications = application.parent
        if applications.name != "applications":
            continue
        root = applications.parent
        roots.setdefault(os.path.normcase(os.fspath(root)), root)
    return [roots[key] for key in sorted(roots)]


def is_owner_controlled_program_path(path) -> bool:
    candidate = os.path.realpath(os.fspath(path))
    return any(
        candidate.casefold() == os.path.realpath(row["path"]).casefold()
        or candidate.casefold().startswith(
            os.path.realpath(row["path"]).casefold() + os.sep
        )
        for row in owner_controlled_program_sources()
    )


def applications_dir() -> Optional[str]:
    """Absolute path to ``openprogram/programs/applications``.

    Computed from the top-level ``openprogram`` package so it works for
    both editable and site-packages installs, and *without* importing
    ``openprogram.programs.workflow`` (which would recurse — this module
    is imported during that package's load).
    """
    try:
        from openprogram.protected_paths import applications_root
        return applications_root()
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
        extra: Short selector name used on the CLI (``openprogram
            programs install <extra>`` → ``gui`` / ``research`` / ``wiki``).
            Just a handle — it does NOT map to an ``openprogram[...]``
            extra anymore; a harness's runtime deps live in the harness's
            own pyproject and are installed from the clone (see
            ``cli/commands/programs.py``).
        repo: HTTPS repo URL (also the ``git clone`` source).
        summary: One-line description for menus / install prompts.
        heavy: True when the program pulls large / native deps (the GUI
            harness pulls torch via ultralytics + OpenCV — declared in
            ITS pyproject, not ours). Used only to warn before install
            and to keep it out of any "auto-install the light ones"
            default.
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
    size_note: str = "repo < 1 MB, no extra deps"
    install_dir: str = ""

    @property
    def repo_dir_name(self) -> str:
        """Folder name the repo clones into (the URL's last segment)."""
        return self.repo.rstrip("/").split("/")[-1]

    def clone_dir(self, base: Optional[str] = None) -> Optional[str]:
        """Absolute path this program is (or would be) cloned to."""
        base = base or applications_dir()
        return os.path.join(base, self.install_dir or self.repo_dir_name) if base else None

    def in_tree_pkg_dir(self, base: Optional[str] = None) -> Optional[str]:
        """Path to the importable package inside an in-tree clone, or None.

        Returns ``<applications>/<package>/<package>`` when that directory
        exists with an ``__init__.py`` — i.e. the program is cloned in.
        """
        candidates: list[str | None] = []
        if base is None:
            aliases = {self.install_dir, self.package, self.repo_dir_name}
            candidates.extend(
                row["path"] for row in owner_controlled_program_sources()
                if os.path.basename(row["path"]) in aliases
            )
        candidates.append(self.clone_dir(base))
        for clone in candidates:
            if not clone:
                continue
            pkg = os.path.join(clone, self.package)
            if os.path.isfile(os.path.join(pkg, "__init__.py")):
                return pkg
        return None

    def git_url(self) -> str:
        """``git clone`` URL pinned to the branch is handled by the caller."""
        return f"{self.repo}.git"

    def is_installed(self) -> bool:
        """True when the program is available to import on this machine.

        In-tree clones under ``programs/applications/`` still need an
        owner-recorded source (or a matching git origin that we migrate).
        A pip/uv-installed distribution counts as owner-controlled.
        A bare ``find_spec`` hit — cwd / PYTHONPATH shadow, no dist-info —
        does not.
        """
        pkg_dir = self.in_tree_pkg_dir()
        if pkg_dir:
            if is_owner_controlled_program_path(pkg_dir):
                return True
            root = os.path.dirname(pkg_dir)
            origin = _catalogued_clone_origin(root, self.repo)
            if origin is not None:
                try:
                    record_program_source(
                        root, source=origin, kind="git-migration",
                    )
                except (OSError, ValueError):
                    pass
                else:
                    if is_owner_controlled_program_path(pkg_dir):
                        return True
        if _has_installed_distribution(self.package):
            return True
        try:
            spec = importlib.util.find_spec(self.package)
        except (ImportError, ValueError):
            return False
        if spec is None:
            return False
        origin = getattr(spec, "origin", None)
        if origin and is_owner_controlled_program_path(origin):
            return True
        for loc in getattr(spec, "submodule_search_locations", None) or ():
            if loc and is_owner_controlled_program_path(loc):
                return True
        return False


def _has_installed_distribution(package: str) -> bool:
    """True when pip/uv left dist-info that owns this import name."""
    try:
        from importlib.metadata import packages_distributions
        return bool(packages_distributions().get(package))
    except Exception:
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
        size_note=("downloads PyTorch: ~300 MB (no NVIDIA GPU) / ~3 GB "
                   "(CUDA); ~1.5 GB on disk"),
        install_dir="gui_harness",
    ),
    Program(
        function="research_agent",
        package="research_harness",
        extra="research",
        repo=f"{_GH}/Research-Agent-Harness",
        summary="Autonomous research agent — from topic to submission-ready paper.",
        heavy=False,  # only depends on openprogram itself
        public=True,
        install_dir="research_harness",
    ),
    Program(
        function="wiki_agent",
        package="wiki_agent_harness",
        extra="wiki",
        repo=f"{_GH}/Wiki-Agent-Harness",
        summary="Personal wiki agent — ingest sessions and organise a knowledge vault.",
        heavy=False,
        public=True,
        size_note="repo < 1 MB; deps: Jinja2 + PyYAML (tiny)",
        install_dir="wiki_agent_harness",
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
    ``programs/applications/<Repo-Name>/<package>``. Programs that aren't
    present are skipped silently (the common case on a base checkout);
    set ``OPENPROGRAM_DEBUG_REGISTRY=1`` to surface import errors of a
    program that *is* present but fails to load.

    Returns the list of function names successfully registered.
    """
    registered: list[str] = []
    for prog in KNOWN_PROGRAMS:
        if not prog.is_installed():
            continue
        # Make an in-tree clone importable by putting its repo dir (the
        # parent of the package) on sys.path.
        pkg_dir = prog.in_tree_pkg_dir()
        if pkg_dir and is_owner_controlled_program_path(pkg_dir):
            repo_dir = os.path.dirname(pkg_dir)
            if repo_dir not in sys.path:
                sys.path.insert(0, repo_dir)
        try:
            # Import the harness's ``agentics`` sub-package — that's the
            # registration contract (it exposes AGENTIC_FUNCTIONS, whose
            # @agentic_function decorators fire on import and self-register).
            # Importing the bare top-level package is NOT enough: the
            # decorators live under ``<package>/agentics/``, which a parent
            # __init__ doesn't pull in (and shouldn't — top-level packages
            # are kept dep-light / lazy). Same contract the auto-discovery
            # path uses, so first-party and third-party register identically.
            importlib.import_module(f"{prog.package}.agentics")
            if prog.function == "gui_agent":
                from openprogram.programs.gui_harness_bridge import (
                    install_gui_harness_web_use,
                )
                install_gui_harness_web_use()
            registered.append(prog.function)
        except Exception as e:  # noqa: BLE001 — never let one break import
            if os.environ.get("OPENPROGRAM_DEBUG_REGISTRY"):
                import traceback
                print(f"[programs] failed to import {prog.package}.agentics: "
                      f"{type(e).__name__}: {e}")
                traceback.print_exc()
    return registered
