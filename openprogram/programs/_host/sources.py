"""Owner-recorded sources of installed programs.

``openprogram programs install <directory | git URL | owner/repo>`` clones
a repository into the user's Programs directory,
``~/.openprogram/programs/<name>/``, and records that directory in
``program-sources.json``; unrecorded directories are never imported.
Nothing installed lives in the source tree: clones that older installers
made under ``openprogram/programs/packages/`` or ``applications/`` are
moved here by ``program_directory.migrate_user_directory``. An existing
development symlink can be recorded explicitly without modifying its
target.

A recorded harness registers through its ``agentics`` SUB-package, not
the top-level package: the registry puts each clone's directory on
``sys.path`` and imports ``<package>.agentics``, whose PROGRAM_ENTRIES
exports register explicit callables (see
``registry._import_external_harness`` and
``docs/capabilities/installing-programs.html``).

Install / remove with::

    openprogram programs install https://github.com/owner/Some-Harness
    openprogram programs uninstall Some-Harness
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from openprogram.host._platform.filesystem import replace_file


_sources_lock = threading.Lock()  # ponytail: one lock for the sources RMW; split if writers contend


def dependency_directory() -> Path:
    """Where a harness's Python dependencies go on an install that cannot hold them.

    A packaged App and a sealed version must not change after they are built:
    pip writing into one breaks its signature or its seal. There is one
    directory per Python minor because compiled modules are tied to it.
    """
    from openprogram.state.paths import get_state_dir

    return Path(get_state_dir()) / "program-dependencies" / f"python{sys.version_info.major}.{sys.version_info.minor}"


def use_dependency_directory() -> None:
    """Make installed harness dependencies importable, when any exist.

    The directory comes after the product's own packages on ``sys.path``, so a
    harness cannot replace what OpenProgram itself runs on.
    """
    directory = str(dependency_directory())
    if directory not in sys.path and Path(directory).is_dir():
        sys.path.append(directory)
        importlib.invalidate_caches()


def _program_sources_path() -> Path:
    from openprogram.state.paths import program_sources_path
    return program_sources_path()


def _read_program_sources_document() -> dict:
    try:
        payload = json.loads(_program_sources_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    rows = payload.get("programs", [])
    payload["version"] = 2
    # Packages load from ~/.openprogram/programs by Git state; records
    # of the former published-Workflow trust model are dropped.
    payload.pop("workflow_projects_migrated", None)
    payload["programs"] = [
        row for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and not _is_workflow_record(row)
    ]
    return payload


def _is_workflow_record(row: dict) -> bool:
    return (row.get("kind") in {"workflow-publish", "workflow-migration"}
            or str(row.get("path", "")).startswith("workflow/"))


def _read_program_sources() -> list[dict]:
    return _read_program_sources_document()["programs"]


def _recorded_root(row: dict) -> str | None:
    """The absolute directory a record names, or None for a malformed row.

    Records are absolute paths. Rows that older versions wrote relative to
    an in-tree catalog (``"scope": "programs"``) name nothing here; the
    user-directory migration rewrites the ones it can still find.
    """
    raw = str(row.get("path", "")).strip()
    if not raw or row.get("scope") is not None:
        return None
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        return None
    return os.path.abspath(expanded)


def _write_program_sources_document(payload: dict) -> None:
    target = _program_sources_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        replace_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_program_sources(rows: list[dict]) -> None:
    payload = _read_program_sources_document()
    payload["version"] = 2
    payload["programs"] = rows
    _write_program_sources_document(payload)


def _update_program_sources(mutate) -> None:
    from openprogram.models.auth.credentials import _private_file_lock
    from openprogram.state.paths import get_state_dir

    target = _program_sources_path()
    with _sources_lock:
        with _private_file_lock(target, root=get_state_dir()):
            mutate(_read_program_sources())


def _is_direct_child(path: str, base: str) -> bool:
    # Check where the install entry lives, not where a deliberate dev
    # symlink points.  The owner records the entry under applications explicitly.
    parent = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    return parent.casefold() == os.path.realpath(base).casefold()


def _source_matches_path(row: dict, root: str) -> bool:
    # Revocation and replacement compare identities, even after the files
    # disappear. Discovery alone requires an existing directory.
    existing = _recorded_root(row)
    return existing is not None and existing.casefold() == root.casefold()


def record_program_source(
    path, *, source: str, kind: str = "git", base: str | None = None
) -> None:
    """Record an owner-approved source before runtime import is allowed.

    The directory must sit directly under ``base`` (default: the user's
    Programs directory) or be a location already recorded.
    """
    from openprogram.state.program_directory import programs_root

    raw = os.fspath(path).strip()
    if not raw:
        raise ValueError("program source path must not be empty")
    root = os.path.abspath(os.path.expanduser(raw))
    allowed = base or str(programs_root())
    if base is None and any(row["path"] == root for row in owner_controlled_program_sources()):
        allowed = str(Path(root).parent)
    if not _is_direct_child(root, allowed) or not os.path.isdir(root):
        raise ValueError("program source must be a directory directly under ~/.openprogram/programs")

    def _mutate(rows: list[dict]) -> None:
        kept = [
            row for row in rows
            if not _source_matches_path(row, root)
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
            if not _source_matches_path(row, root)
        ]
        _write_program_sources(kept)

    _update_program_sources(_mutate)


def owner_controlled_program_sources(base: str | None = None) -> list[dict]:
    """Return valid owner-recorded roots, optionally limited to one directory."""
    out = []
    for row in _read_program_sources():
        if row.get("kind") == "application":
            # Application packages execute only through their isolated runner.
            continue
        root = _recorded_root(row)
        if root is None:
            continue
        if os.path.isdir(root) and (base is None or _is_direct_child(root, base)):
            out.append({**row, "path": root})
    return out


def is_owner_controlled_program_path(path) -> bool:
    candidate = os.path.realpath(os.fspath(path))
    return any(
        candidate.casefold() == os.path.realpath(row["path"]).casefold()
        or candidate.casefold().startswith(
            os.path.realpath(row["path"]).casefold() + os.sep
        )
        for row in owner_controlled_program_sources()
    )
