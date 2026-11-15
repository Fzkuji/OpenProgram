"""The user's Programs directory, resolved without importing the registry.

Every Program the user adds lives in ``<state>/programs/`` (``~/.openprogram/
programs/`` for the default profile, ``~/.openprogram-<profile>/programs/``
for a named one)::

    programs/
      <name>/          a program source directory: a Python package with
                       PROGRAM_ENTRIES (its own Git repository), an
                       application directory with application.json, or both
      catalog.json     applications recorded by source directory, with their
                       enabled / hidden / display_title settings
      .runtime/        what the application runtime keeps: data/, runs/,
                       environments/, state.sqlite and the copied versions/
                       of older installs
      .removed/        directories set aside by ``openprogram programs
                       uninstall``

Names that start with ``.`` or ``_`` and plain files are never Programs.
Nothing a user installs lives inside the ``openprogram`` source tree;
:func:`migrate_user_directory` moves what older versions put there.

This module sits outside ``openprogram.programs`` because importing that
package loads every Program; the worker calls :func:`migrate_user_directory`
before it does.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable
from openprogram.host._platform.filesystem import replace_file

logger = logging.getLogger(__name__)

DIRECTORY = "programs"
CATALOG_FILE = "catalog.json"
RUNTIME_DIR = ".runtime"
REMOVED_DIR = ".removed"
PACKAGE_MARKER = "__init__.py"
APPLICATION_MARKER = "application.json"

# The layout this directory replaces: ``workflows/`` held the Python
# packages, ``applications/`` the catalog and the application runtime.
_LEGACY_PACKAGES = "workflows"
_LEGACY_APPLICATIONS = "applications"
_CATALOG_FILES = {CATALOG_FILE, CATALOG_FILE + ".lock"}


def _state_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir())


def programs_root(state_dir: str | os.PathLike | None = None) -> Path:
    """``<state>/programs``: the user's program source directories."""
    return Path(state_dir or _state_dir()) / DIRECTORY


def catalog_path(state_dir: str | os.PathLike | None = None) -> Path:
    """The application catalog, ``<state>/programs/catalog.json``."""
    return programs_root(state_dir) / CATALOG_FILE


def runtime_root(state_dir: str | os.PathLike | None = None) -> Path:
    """Where the application runtime keeps data, runs and environments."""
    return programs_root(state_dir) / RUNTIME_DIR


def removed_root(state_dir: str | os.PathLike | None = None) -> Path:
    return programs_root(state_dir) / REMOVED_DIR


def program_kinds(directory: Path) -> list[str]:
    """``package`` and/or ``application``: what a source directory holds."""
    kinds = []
    if (directory / PACKAGE_MARKER).is_file():
        kinds.append("package")
    if (directory / APPLICATION_MARKER).is_file():
        kinds.append("application")
    return kinds


def program_dirs(root: Path | None = None) -> dict[str, Path]:
    """Every program source directory directly under ``root``, by name.

    A directory is one when it holds ``__init__.py`` or
    ``application.json``. Hidden and underscored names, symbolic links
    and files are skipped.
    """
    root = root or programs_root()
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if child.name.startswith((".", "_")) or child.is_symlink() or not child.is_dir():
            continue
        if program_kinds(child):
            found[child.name] = child
    return found


# ---------------------------------------------------------------------------
# Migration from workflows/ + applications/
# ---------------------------------------------------------------------------


def _remove_if_empty(directory: Path, log: Callable[..., None]) -> None:
    junk = directory / ".DS_Store"
    try:
        if junk.is_file() and [p.name for p in directory.iterdir()] == [".DS_Store"]:
            junk.unlink()
        directory.rmdir()
    except OSError:
        log("programs migration: left %s in place; it still holds %s", directory,
            ", ".join(sorted(p.name for p in directory.iterdir())))
        return
    log("programs migration: removed the empty directory %s", directory)


def _rewrite(value: object, prefixes: list[tuple[str, str]]) -> object:
    if not isinstance(value, str):
        return value
    for old, new in prefixes:
        if value == old or value.startswith(old + os.sep):
            return new + value[len(old):]
    return value


def _rewrite_catalog(state: Path, log: Callable[..., None],
                     moved: list[tuple[str, str]] = ()) -> int:
    """Point catalog rows at the moved directories. Returns rows changed.

    ``moved`` adds ``(old directory, new directory)`` pairs to the fixed
    ``workflows/`` and ``applications/`` prefixes.
    """
    target = catalog_path(state)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    rows = document.get("applications") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        return 0
    prefixes: list[tuple[str, str]] = []
    for base in dict.fromkeys((str(state), os.path.realpath(state))):
        prefixes.append((os.path.join(base, _LEGACY_APPLICATIONS, "versions"),
                         str(runtime_root(state) / "versions")))
        prefixes.append((os.path.join(base, _LEGACY_PACKAGES), str(programs_root(state))))
    prefixes.extend(moved)
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        before = json.dumps(row, sort_keys=True)
        for key in ("path", "source"):
            if key in row:
                row[key] = _rewrite(row[key], prefixes)
        definition = row.get("application")
        if isinstance(definition, dict):
            for key in ("path", "source"):
                if key in definition:
                    definition[key] = _rewrite(definition[key], prefixes)
        changed += json.dumps(row, sort_keys=True) != before
    if changed:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
            replace_file(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        log("programs migration: pointed %d catalog row(s) at %s", changed, programs_root(state))
    return changed


def migrate_user_directory(
    state_dir: str | os.PathLike | None = None,
    *,
    log: Callable[..., None] | None = None,
) -> list[tuple[str, str]]:
    """Move ``workflows/`` and ``applications/`` into ``programs/``.

    ``workflows/<name>`` becomes ``programs/<name>``; the application
    catalog (``applications/catalog.json``) becomes
    ``programs/catalog.json``; everything else the application runtime
    kept in ``applications/`` (``data/``, ``runs/``, ``environments/``,
    ``staging/``, ``versions/``, ``state.sqlite``) moves under
    ``programs/.runtime/``. Catalog rows that pointed into the old
    directories are rewritten. Each item is renamed, never copied; an item
    whose destination already exists stays where it is and is logged. The
    emptied old directories are removed. Harness clones that older
    installers made inside the source tree move too
    (:func:`migrate_source_tree_clones`). Running it again finds nothing to
    do. Returns ``(source, destination)`` for every move.
    """
    log = log or logger.info
    state = Path(state_dir) if state_dir is not None else _state_dir()
    root = programs_root(state)
    runtime = runtime_root(state)
    moves: list[tuple[str, str]] = []

    def move(source: Path, destination: Path, *, merge: bool = False) -> None:
        if destination.exists() or destination.is_symlink():
            # Runtime state (runs, data, environments, ...) merges into a
            # directory the new code may already have created, item by
            # item; a program directory never merges into another one.
            if merge and source.is_dir() and destination.is_dir() and not (
                source.is_symlink() or destination.is_symlink()
            ):
                for child in sorted(source.iterdir()):
                    move(child, destination / child.name, merge=True)
                _remove_if_empty(source, log)
                return
            log("programs migration: kept %s; %s already exists", source, destination)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(source), os.fspath(destination))
        log("programs migration: moved %s -> %s", source, destination)
        moves.append((os.fspath(source), os.fspath(destination)))

    packages = state / _LEGACY_PACKAGES
    if packages.is_dir() and not packages.is_symlink():
        for child in sorted(packages.iterdir()):
            if child.name != ".DS_Store":
                move(child, root / child.name)
        _remove_if_empty(packages, log)

    applications = state / _LEGACY_APPLICATIONS
    if applications.is_dir() and not applications.is_symlink():
        for child in sorted(applications.iterdir()):
            if child.name == ".DS_Store":
                continue
            if child.name in _CATALOG_FILES:
                move(child, root / child.name)
            else:
                move(child, runtime / child.name, merge=True)
        _remove_if_empty(applications, log)

    clone_moves = migrate_source_tree_clones(state, log=log)
    moves.extend(clone_moves)
    _rewrite_catalog(state, log, clone_moves)
    return moves


# ---------------------------------------------------------------------------
# Migration of harness clones out of the source tree
# ---------------------------------------------------------------------------

_SOURCES_FILE = "program-sources.json"
# Where older installers cloned harnesses, relative to openprogram/programs/.
_SOURCE_TREE_CONTAINERS = ("packages", "applications")


def _source_tree_catalogs(document: dict) -> list[Path]:
    """The ``openprogram/programs`` directories older records may point into:
    the running package's and the checkout recorded as ``catalog_root``."""
    import openprogram

    catalogs = [Path(openprogram.__file__).resolve().parent / "programs"]
    bound = document.get("catalog_root")
    if isinstance(bound, str) and os.path.isabs(bound) and Path(bound).is_dir():
        catalogs.append(Path(bound).resolve())
    return list(dict.fromkeys(catalogs))


def _source_tree_clone(row: dict, catalogs: list[Path]) -> Path | None:
    """The in-tree clone directory a ``program-sources.json`` row names, if any.

    A row is either relative to a catalog (``"scope": "programs"``,
    ``"path": "applications/<name>"``) or an absolute path whose parent is
    ``.../openprogram/programs/{packages,applications}``. A symbolic link is
    a development link the owner made and stays where it is.
    """
    raw = str(row.get("path", "")).strip()
    if row.get("kind") == "application" or not raw:
        return None
    if row.get("scope") is not None:
        parts = raw.split("/")
        if (row.get("scope") != "programs" or len(parts) != 2
                or parts[0] not in _SOURCE_TREE_CONTAINERS or parts[1] in {"", ".", ".."}):
            return None
        found = [catalog / raw for catalog in catalogs
                 if (catalog / raw).is_dir() and not (catalog / raw).is_symlink()]
        return found[0] if found else None
    candidate = Path(os.path.expanduser(raw))
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        return None
    parent = candidate.parent
    if (parent.name in _SOURCE_TREE_CONTAINERS and parent.parent.name == "programs"
            and parent.parent.parent.name == "openprogram"):
        return candidate
    return None


def migrate_source_tree_clones(
    state_dir: str | os.PathLike | None = None,
    *,
    log: Callable[..., None] | None = None,
) -> list[tuple[str, str]]:
    """Move recorded harness clones from the source tree into ``programs/``.

    Older installers cloned harnesses into ``openprogram/programs/applications/<name>``
    or ``packages/<name>`` and recorded them in ``program-sources.json``.
    Each recorded clone that is still there is renamed to
    ``<state>/programs/<name>`` and its row is rewritten to that absolute
    path; rows of the same clone are merged into one, and rows that named a
    catalog-relative location nothing resolves to any more are dropped,
    as is the ``catalog_root`` binding. A clone whose destination already
    exists stays where it is and is logged. Only clones recorded by this
    profile move, so another profile never takes them. Running it again
    finds nothing to do. Returns ``(source, destination)`` for every move.
    """
    from openprogram.models.auth.credentials import _private_file_lock

    log = log or logger.info
    state = Path(state_dir) if state_dir is not None else _state_dir()
    target = state / _SOURCES_FILE
    if not target.is_file():
        return []
    root = programs_root(state)
    moves: list[tuple[str, str]] = []
    with _private_file_lock(target, root=state):
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = document.get("programs") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            return []
        catalogs = _source_tree_catalogs(document)
        relocated: dict[str, str] = {}
        kept: list[dict] = []
        changed = "catalog_root" in document
        for row in rows:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            clone = _source_tree_clone(row, catalogs)
            if clone is None:
                if row.get("scope") is not None:
                    # Catalog-relative records are no longer read.
                    changed = True
                    log("programs migration: dropped the record of %s; nothing is there any more",
                        row.get("path"))
                    continue
                kept.append(row)
                continue
            key = os.fspath(clone.resolve())
            if key not in relocated:
                destination = root / clone.name
                if destination.exists() or destination.is_symlink():
                    log("programs migration: kept %s; %s already exists", clone, destination)
                    relocated[key] = os.fspath(clone)
                else:
                    root.mkdir(parents=True, exist_ok=True)
                    shutil.move(os.fspath(clone), os.fspath(destination))
                    log("programs migration: moved %s -> %s", clone, destination)
                    moves.append((os.fspath(clone), os.fspath(destination)))
                    relocated[key] = os.fspath(destination)
            new_path = relocated[key]
            if any(isinstance(other, dict) and other.get("path") == new_path and other.get("scope") is None
                   for other in kept):
                changed = True
                continue
            record = {key_: value for key_, value in row.items() if key_ not in {"scope", "entity_kind"}}
            record["path"] = new_path
            kept.append(record)
            changed = changed or record != row
        if changed:
            document.pop("catalog_root", None)
            document["programs"] = kept
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
                replace_file(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    return moves


__all__ = [
    "APPLICATION_MARKER", "CATALOG_FILE", "PACKAGE_MARKER", "RUNTIME_DIR",
    "catalog_path", "migrate_source_tree_clones", "migrate_user_directory",
    "program_dirs", "program_kinds",
    "programs_root", "removed_root", "runtime_root",
]
