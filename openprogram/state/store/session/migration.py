"""Recoverable move of working-folder sessions into application-owned storage."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from openprogram.state.store.session.atomic_file import atomic_write_text, read_text_with_retry
from openprogram.state.store.session.placement import (
    external_recovery_dir,
    is_deleted,
    legacy_project_session_dir,
    move_session_folder,
    nested_session_dir,
    session_looks_present,
)
from openprogram.state.store.session.session_log import SessionLog
from openprogram.state.store.session.session_lock import (
    registry_file_lock,
    session_interprocess_lock,
    session_lock_available,
)

_log = logging.getLogger(__name__)

STAGES = ("inventory", "copy", "verify", "publish", "cleanup", "done")


def journal_path(root: Path) -> Path:
    return Path(root) / ".migration" / "journal.json"


def staging_dir(root: Path, session_id: str) -> Path:
    return Path(root) / ".migration" / "staging" / session_id


def hold_path(root: Path, session_id: str) -> Path:
    return Path(root) / ".migration" / "holds" / session_id


def load_journal(root: Path, *, strict: bool = False) -> dict[str, Any]:
    """The migration journal, or an empty one when there is none.

    An unreadable journal reads as empty unless ``strict``, which raises
    instead so callers that would destroy data can tell "no migration"
    from "migration state unknown".
    """
    with registry_file_lock(root, "migration-journal"):
        return _load_journal_unlocked(root, strict=strict)


def _load_journal_unlocked(root: Path, *, strict: bool = False) -> dict[str, Any]:
    path = journal_path(root)
    if not path.is_file():
        return {"version": 1, "sessions": {}}
    try:
        data = json.loads(read_text_with_retry(path))
        if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
            return data
        if strict:
            raise ValueError(f"migration journal malformed at {path}")
    except (OSError, json.JSONDecodeError):
        if strict:
            raise
        _log.warning("migration journal unreadable at %s", path)
    return {"version": 1, "sessions": {}}


def update_journal_row(root: Path, session_id: str, row: dict[str, Any]) -> None:
    """Update one session row without writing a stale snapshot of others."""
    with registry_file_lock(root, "migration-journal"):
        journal = _load_journal_unlocked(root)
        journal.setdefault("sessions", {})[session_id] = dict(row)
        path = journal_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(journal, indent=2, ensure_ascii=False, default=str))


def session_hold_active(root: Path, session_id: str) -> bool:
    return hold_path(root, session_id).is_file()


def _set_hold(root: Path, session_id: str) -> None:
    path = hold_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def _clear_hold(root: Path, session_id: str) -> None:
    try:
        hold_path(root, session_id).unlink()
    except FileNotFoundError:
        return


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_tree(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for current, dirs, files in os.walk(root, followlinks=False):
        kept_dirs = []
        for name in dirs:
            path = Path(current) / name
            if path.is_symlink():
                rows.append({"rel": str(path.relative_to(root)), "kind": "symlink",
                             "target": os.readlink(path)})
            else:
                kept_dirs.append(name)
        dirs[:] = [name for name in kept_dirs if name not in {".", ".."}]
        base = Path(current)
        for name in files:
            path = base / name
            if path.is_symlink():
                rows.append({
                    "rel": str(path.relative_to(root)),
                    "kind": "symlink",
                    "target": os.readlink(path),
                })
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            rows.append({
                "rel": str(path.relative_to(root)),
                "kind": "file",
                "size": info.st_size,
                "sha256": _file_digest(path),
            })
    rows.sort(key=lambda row: row["rel"])
    return rows


def _copy_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=False)


def _verify_inventory(dest: Path, inventory: list[dict[str, Any]]) -> None:
    current = {row["rel"]: row for row in _inventory_tree(dest)}
    if len(current) != len(inventory):
        raise RuntimeError("migration inventory size mismatch")
    for row in inventory:
        other = current.get(row["rel"])
        if other != row:
            raise RuntimeError(f"migration inventory mismatch: {row['rel']}")


def _fsync_tree(path: Path) -> None:
    """Flush every copied file before it can be published.

    File flush failures are part of the migration transaction: ignoring one
    would allow an apparently verified copy to be published and the legacy
    source to be removed without a durable destination.  Directory flushes
    are handled separately because Windows does not expose directory file
    descriptors.
    """
    directories: list[Path] = []
    for current, dirs, files in os.walk(path, topdown=False, followlinks=False):
        directories.extend(
            Path(current) / name for name in dirs
            if not (Path(current) / name).is_symlink()
        )
        for name in files:
            file_path = Path(current) / name
            if file_path.is_symlink():
                # The link itself has no file data to flush.  Opening it would
                # follow an external target and could flush unrelated data.
                continue
            # Windows FlushFileBuffers requires a writable file handle.
            flags = os.O_RDWR if os.name == "nt" else os.O_RDONLY
            fd = os.open(file_path, flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    if os.name == "nt":
        return
    directories.append(path)
    for directory in directories:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Durably publish one directory entry without scanning siblings."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _live_jobs(session_id: str) -> bool:
    """Whether an execution, a chat turn or a Job is still live on the session.

    Only the runtime can tell; it answers through ``execution_facts``,
    and an unknown state counts as live (migration fails closed).
    """
    from openprogram.state.execution_facts import session_has_live_work

    return session_has_live_work(session_id)


def quiesce_session(root: Path, session_id: str, *, timeout: float = 15.0) -> bool:
    """Stop accepting new writers via hold file; wait for exclusive lock.

    Never kills tasks. Returns False when writers cannot drain.
    """
    _set_hold(root, session_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _live_jobs(session_id):
            time.sleep(0.1)
            continue
        if not session_lock_available(session_id, root=root):
            time.sleep(0.1)
            continue
        try:
            with session_interprocess_lock(session_id, timeout=1.0, root=root):
                return True
        except (TimeoutError, BlockingIOError):
            continue
    return False


def collect_legacy_candidates(store, project_id: str | None = None) -> list[dict[str, Any]]:
    from openprogram.state.store.project import project_store as projects

    root = Path(store.root_path)
    locations = dict(getattr(store, "_locations", {}) or {})
    try:
        locations.update(store._load_locations())
    except Exception:
        _log.debug("failed to load session locations", exc_info=True)
    found: dict[str, dict[str, Any]] = {}
    for project in projects.list_projects():
        if project_id is not None and project.id != project_id:
            continue
        if project.is_default:
            continue
        if not getattr(project, "directory_identity", "") and not getattr(project, "native_bookmark", ""):
            # Legacy records cannot prove that the current path is the
            # original directory. Require explicit locate before migration.
            _mark_project(project.id, "pending", "directory identity unavailable")
            continue
        for session_id in list(project.session_ids or []):
            if is_deleted(root, session_id):
                continue
            nested = nested_session_dir(root, project.id, session_id)
            if session_looks_present(nested):
                continue
            source = None
            loc = locations.get(session_id)
            if loc and session_looks_present(Path(loc)):
                source = Path(loc)
            legacy = legacy_project_session_dir(project.path, session_id) if project.path else None
            if source is None and legacy is not None and session_looks_present(legacy):
                source = legacy
            if source is None:
                default = root / session_id
                if session_looks_present(default) and project.path:
                    # default-root sessions are already home-owned
                    continue
                found[session_id] = {
                    "session_id": session_id,
                    "project_id": project.id,
                    "source": str(legacy or loc or ""),
                    "source_unavailable": True,
                }
                continue
            # Already under the state root (default layout) — not a workdir copy.
            try:
                source.relative_to(root)
                continue
            except ValueError:
                pass
            found[session_id] = {
                "session_id": session_id,
                "project_id": project.id,
                "source": str(source),
                "source_unavailable": False,
            }
    return list(found.values())


def migrate_session(store, entry: dict[str, Any], *, timeout: float = 15.0) -> str:
    root = Path(store.root_path)
    session_id = entry["session_id"]
    with registry_file_lock(root, f"migration-{session_id}", timeout=timeout):
        return _migrate_session_once(store, entry, timeout=timeout)


def _migrate_session_once(store, entry: dict[str, Any], *, timeout: float = 15.0) -> str:
    """Migrate one legacy session. Returns done|pending|deferred|failed."""
    root = Path(store.root_path)
    session_id = entry["session_id"]
    project_id = entry["project_id"]
    journal = load_journal(root)
    durable = dict(journal.get("sessions", {}).get(session_id) or {})
    durable_destination = durable.get("destination")
    if durable.get("stage") == "done" and durable_destination and session_looks_present(Path(durable_destination)):
        return "done"
    row = dict(durable or entry)
    protected = {"stage", "error", "source_unavailable", "destination", "inventory",
                 "recovery_inventory", "recovery_source"}
    row.update({key: value for key, value in entry.items() if key not in protected})
    if is_deleted(root, session_id):
        row["stage"] = "deleted"
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        return "deleted"
    dest = nested_session_dir(root, project_id, session_id)
    row["destination"] = str(dest)
    source_present = bool(entry.get("source")) and session_looks_present(Path(entry["source"]))
    if not source_present and _holds_session(dest, session_id):
        # The copy finished and the old workdir copy is gone; there is
        # nothing left to wait for.
        row["stage"] = "done"
        row["source_unavailable"] = False
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        return "done"
    if entry.get("source_unavailable"):
        row["stage"] = "pending"
        row["source_unavailable"] = True
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        _mark_project(project_id, "pending")
        return "pending"
    source = Path(entry["source"])
    if not session_looks_present(source):
        row["stage"] = "pending"
        row["source_unavailable"] = True
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        _mark_project(project_id, "pending")
        return "pending"
    if session_looks_present(dest) and row.get("stage") == "done":
        row["stage"] = "done"
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        return "done"
    if not quiesce_session(root, session_id, timeout=timeout):
        row["stage"] = "deferred"
        row["error"] = "writers could not quiesce"
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        _clear_hold(root, session_id)
        return "deferred"
    try:
        with store._session_lock(session_id):
            with session_interprocess_lock(
                session_id, timeout=timeout, root=root,
            ):
                return _migrate_locked(store, root, journal, row, source, dest)
    except Exception as exc:
        row["stage"] = "failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        journal.setdefault("sessions", {})[session_id] = row
        update_journal_row(root, session_id, row)
        _mark_project(row.get("project_id"), "error", str(exc))
        return "failed"
    finally:
        _clear_hold(root, session_id)


def _migrate_locked(store, root, journal, row, source: Path, dest: Path) -> str:
    session_id = row["session_id"]
    project_id = row["project_id"]
    staged = staging_dir(root, session_id)
    recovery_source = external_recovery_dir(source)
    legacy_internal = source / "file_backups"
    recovery_dest = external_recovery_dir(dest)
    staged_recovery = staged.parent / f"{session_id}.recovery"

    row["stage"] = "inventory"
    inventory = _inventory_tree(source)
    recovery_inventory = []
    if recovery_source.is_dir():
        recovery_inventory = _inventory_tree(recovery_source)
    elif legacy_internal.is_dir():
        recovery_source = legacy_internal
        recovery_inventory = _inventory_tree(legacy_internal)
    row["inventory"] = inventory
    row["recovery_inventory"] = recovery_inventory
    row["recovery_source"] = str(recovery_source) if recovery_inventory else ""
    journal.setdefault("sessions", {})[session_id] = row
    update_journal_row(root, session_id, row)

    row["stage"] = "copy"
    update_journal_row(root, session_id, row)
    _copy_tree(source, staged / "session")
    if recovery_inventory:
        _copy_tree(Path(row["recovery_source"]), staged_recovery)

    row["stage"] = "verify"
    update_journal_row(root, session_id, row)
    _verify_inventory(staged / "session", inventory)
    source_after = _inventory_tree(source)
    if source_after != inventory:
        raise RuntimeError("source changed during migration copy")
    if recovery_inventory:
        _verify_inventory(staged_recovery, recovery_inventory)
    _fsync_tree(staged / "session")
    if recovery_inventory:
        _fsync_tree(staged_recovery)

    # A previous run may have published the session and failed while
    # publishing external recovery or locations.json. Verify the published
    # session against the durable inventory and resume only the missing
    # publication. A conflicting pre-existing destination is an error.
    if dest.exists():
        _verify_inventory(dest, inventory)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        move_session_folder(staged / "session", dest)
    row["stage"] = "publish"
    update_journal_row(root, session_id, row)
    _fsync_dir(dest.parent)
    if recovery_inventory:
        recovery_dest.parent.mkdir(parents=True, exist_ok=True)
        if recovery_dest.exists():
            _verify_inventory(recovery_dest, recovery_inventory)
        elif staged_recovery.exists():
            move_session_folder(staged_recovery, recovery_dest)
        else:
            raise RuntimeError("external recovery publication is missing")
        _fsync_dir(recovery_dest.parent)
    store._record_location(session_id, dest)
    store._sessions.pop(session_id, None)
    row["stage"] = "cleanup"
    update_journal_row(root, session_id, row)
    try:
        shutil.rmtree(source)
    except OSError as exc:
        _log.warning("legacy session source not removed %s: %s", source, exc)
    if row.get("recovery_source"):
        rec = Path(row["recovery_source"])
        if rec.is_dir() and rec != recovery_dest:
            try:
                shutil.rmtree(rec)
            except OSError:
                pass
    row["stage"] = "done"
    row["error"] = ""
    row["source_unavailable"] = False
    journal["sessions"][session_id] = row
    update_journal_row(root, session_id, row)
    _mark_project(project_id, "available")
    try:
        shutil.rmtree(staged, ignore_errors=True)
    except OSError:
        pass
    return "done"


def _holds_session(path: Path, session_id: str) -> bool:
    """Whether ``path`` is a complete copy of this session, not a stranger."""
    log = SessionLog(path)
    return log.exists() and log.read_meta().get("id") == session_id


def _mark_project(project_id: str | None, state: str, error: str = "") -> None:
    if not project_id:
        return
    try:
        from openprogram.state.store.project import project_store as projects
        projects.set_location_state(project_id, state, error=error)
    except Exception:
        _log.debug("project %s state %s not stored", project_id, state, exc_info=True)


def run_startup_migration(store=None, *, timeout: float = 15.0) -> dict[str, str]:
    """Quiescent boundary: copy legacy workdir sessions before execution."""
    if store is None:
        from openprogram.state.store.session.session_store import default_store
        store = default_store()
    results: dict[str, str] = {}
    journal = load_journal(Path(store.root_path))
    pending_rows = list((journal.get("sessions") or {}).values())
    candidates = {row["session_id"]: row for row in pending_rows if row.get("session_id")}
    for entry in collect_legacy_candidates(store):
        previous = candidates.get(entry["session_id"], {})
        merged = dict(previous)
        merged.update(entry)
        candidates[entry["session_id"]] = merged
    for entry in candidates.values():
        stage = entry.get("stage")
        if stage == "done":
            results[entry["session_id"]] = "done"
            continue
        results[entry["session_id"]] = migrate_session(store, entry, timeout=timeout)
    return results


def run_project_migration(project_id: str, store=None, *, timeout: float = 15.0) -> dict[str, str]:
    """Run the same recoverable migration boundary for one project."""
    if store is None:
        from openprogram.state.store.session.session_store import default_store
        store = default_store()
    root = Path(store.root_path)
    journal = load_journal(root)
    candidates = {
        row["session_id"]: row
        for row in (journal.get("sessions") or {}).values()
        if row.get("session_id") and row.get("project_id") == project_id
    }
    for entry in collect_legacy_candidates(store, project_id=project_id):
        previous = candidates.get(entry["session_id"], {})
        merged = dict(previous)
        merged.update(entry)
        candidates[entry["session_id"]] = merged
    results = {}
    for entry in candidates.values():
        results[entry["session_id"]] = (
            "done" if entry.get("stage") == "done" else
            migrate_session(store, entry, timeout=timeout)
        )
    return results
