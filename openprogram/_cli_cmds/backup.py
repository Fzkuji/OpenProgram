"""Create, list, and restore snapshots of the profile state dir.

Scope is an explicit allowlist of top-level entries under the state dir
(``~/.openprogram/`` or ``~/.openprogram-<profile>/``) — the things that
represent *what the user built*: memory workspace, sessions, config,
programs/functions metadata, channel bindings, agents, skills, plugins.

Everything else is excluded by construction, because an allowlist can't
silently start capturing a new cache directory the way a denylist would.
Caches, trash, logs, locks/pids/ports, the web token, ``node_modules``,
and browser profiles are all regenerated on next start and would only
bloat the archive.

Credentials are the one deliberate opt-out: ``auth/`` and ``mcp_tokens/``
hold live secrets in file form and are skipped unless the user passes
``--include-credentials``, which prints a plaintext-credential warning.

Archives land in ``<state>/backups/`` as ``<profile>-<timestamp>.tar.gz``
with mode 0600.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

# Top-level entries worth preserving. Anything not listed is skipped.
# Keep this list in sync with docs/server/backup.md when it changes.
INCLUDED: tuple[str, ...] = (
    "memory",  # memory workspace (core.md, topics, timeline, ...)
    "sessions",  # session transcripts on disk
    "sessions.db",  # session index
    "session_aliases.json",
    "config.json",  # main configuration
    "cli-config.json",
    "agents",  # per-agent definitions
    "agents.json",
    "programs_meta.json",
    "functions_meta.json",
    "program-sources.json",
    "channels",  # channel account state
    "bindings.json",  # channel <-> session bindings
    "skills",
    "skills.json",
    "plugins",
    "marketplaces.json",
    "mcp_servers.json",
    "models",
    "commands",
    "owner.json",
    "projects",
    "profiles",  # account metadata; credentials are inventory-filtered
    "worktrees.json",
    "usage.db",  # usage/accounting history
)

# Whole top-level credential trees included only with --include-credentials.
CREDENTIAL_ENTRIES: tuple[str, ...] = ("auth", "mcp_tokens")

_MANIFEST_NAME = "backup-manifest.json"

# Never archived, even if nested inside an included entry.
_SKIP_NAMES = frozenset({"node_modules", "__pycache__", ".DS_Store"})
_SKIP_SUFFIXES = (".lock", ".pid", ".port", ".log", ".sock")


def _state_dir() -> Path:
    from openprogram.paths import get_state_dir

    return get_state_dir()


def _profile_name() -> str:
    from openprogram.paths import get_active_profile

    return get_active_profile() or "default"


def backups_dir() -> Path:
    state = _state_dir()
    state.mkdir(parents=True, exist_ok=True)
    from openprogram.credential_files import _ensure_private_directory

    return _ensure_private_directory(state / "backups", root=state)


def _excluded(path: Path) -> bool:
    """True if this member should be left out of the archive."""
    if path.name in _SKIP_NAMES:
        return True
    if path.name.endswith(_SKIP_SUFFIXES):
        return True
    # Symlinks are skipped rather than followed: a link out of the state
    # dir would silently pull unrelated (possibly huge, possibly secret)
    # trees into the archive.
    return path.is_symlink()


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _entries_to_archive(state: Path, include_credentials: bool) -> list[str]:
    names = list(INCLUDED)
    if include_credentials:
        names += list(CREDENTIAL_ENTRIES)
    return [n for n in names if (state / n).exists()]


def create_backup(
    include_credentials: bool = False,
    label: str | None = None,
) -> Path:
    """Write a tar.gz of the in-scope state and return its path.

    Raises OSError if the archive can't be written or read back.
    """
    state = _state_dir()
    out_dir = backups_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = out_dir / f"{_profile_name()}{suffix}-{stamp}.tar.gz"

    from openprogram.credential_files import (
        SECRET_INVENTORY,
        _private_atomic_write,
        backup_bytes,
        inventory_for_path,
    )

    included_secret_kinds: set[str] = set()
    redacted_secret_kinds: set[str] = set()
    excluded_secret_kinds: set[str] = set()

    def _selector_has_secret(node: object, parts: list[str]) -> bool:
        if not isinstance(node, dict) or not parts:
            return False
        head, *tail = parts
        if head == "*":
            return any(_selector_has_secret(value, tail) for value in node.values())
        if head not in node:
            return False
        if tail:
            return _selector_has_secret(node[head], tail)
        value = node[head]
        return (
            bool(value)
            if isinstance(value, (str, bytes, dict, list))
            else value is not None
        )

    def _entry_has_secret(entry, raw: bytes) -> bool:
        if entry.whole_file:
            return True
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return True
        return any(
            _selector_has_secret(payload, selector.split("."))
            for selector in entry.secret_fields
        )

    def _profile_member_allowed(source: Path, arcname: str) -> bool:
        parts = Path(arcname).parts
        if not parts or parts[0] != "profiles":
            return True
        if len(parts) <= 2:
            return True
        if len(parts) == 3:
            return parts[2] in {"metadata.json", ".env", "auth"}
        if parts[2] != "auth":
            return False
        return source.is_dir() or bool(inventory_for_path(arcname))

    def _is_secret_writer_temporary(arcname: str) -> bool:
        if not arcname.endswith(".tmp"):
            return False
        if inventory_for_path(arcname[: -len(".tmp")]):
            return True
        parts = Path(arcname).parts
        if parts and parts[0] in {"auth", "mcp_tokens"}:
            return True
        if len(parts) >= 3 and parts[0] == "profiles" and parts[2] == "auth":
            return True
        return (
            len(parts) >= 5
            and parts[0] == "channels"
            and parts[-1].startswith("access-")
            and parts[-1].endswith(".json.tmp")
        )

    if not include_credentials:
        for top_name in CREDENTIAL_ENTRIES:
            credential_root = state / top_name
            if not credential_root.is_dir():
                continue
            for source in credential_root.rglob("*"):
                arcname = source.relative_to(state).as_posix()
                if (
                    not source.is_file()
                    or _excluded(source)
                    or _is_secret_writer_temporary(arcname)
                ):
                    continue
                excluded_secret_kinds.update(
                    entry.kind
                    for entry in inventory_for_path(arcname)
                    if entry.backup_policy == "include_on_opt_in"
                )

    def _add_path(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
        if (
            _excluded(source)
            or not _profile_member_allowed(source, arcname)
            or _is_secret_writer_temporary(arcname)
        ):
            return
        info = tar.gettarinfo(str(source), arcname=arcname)
        if info.issym() or info.islnk():
            return
        if info.isdir():
            tar.addfile(info)
            for child in sorted(source.iterdir(), key=lambda item: item.name):
                _add_path(tar, child, f"{arcname}/{child.name}")
            return
        if not info.isfile():
            return

        inventory = inventory_for_path(arcname)
        if inventory:
            raw = source.read_bytes()
            archived = backup_bytes(
                arcname,
                raw,
                include_credentials=include_credentials,
            )
            if archived is None:
                excluded_secret_kinds.update(entry.kind for entry in inventory)
                return
            for entry in inventory:
                if not _entry_has_secret(entry, raw):
                    continue
                if entry.backup_policy == "never_backup":
                    redacted_secret_kinds.add(entry.kind)
                elif include_credentials:
                    included_secret_kinds.add(entry.kind)
                elif entry.backup_policy == "redact_default":
                    redacted_secret_kinds.add(entry.kind)
                else:
                    excluded_secret_kinds.add(entry.kind)
            if archived != raw:
                info.size = len(archived)
                tar.addfile(info, io.BytesIO(archived))
                return
        tar.add(str(source), arcname=arcname, recursive=False)

    def _write_archive(handle) -> None:
        with tarfile.open(fileobj=handle, mode="w:gz") as tar:
            for name in _entries_to_archive(state, include_credentials):
                _add_path(tar, state / name, name)
            manifest = {
                "format_version": 1,
                "credential_opt_in": include_credentials,
                "credentials_included": bool(included_secret_kinds),
                "included_secret_kinds": sorted(included_secret_kinds),
                "redacted_secret_kinds": sorted(redacted_secret_kinds),
                "excluded_secret_kinds": sorted(excluded_secret_kinds),
                "credential_policy": {
                    "never_backed_up_secret_kinds": sorted(
                        {
                            entry.kind
                            for entry in SECRET_INVENTORY
                            if entry.backup_policy == "never_backup"
                        }
                    )
                },
            }
            payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            info = tarfile.TarInfo(_MANIFEST_NAME)
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(payload))

    _private_atomic_write(target, _write_archive, root=state)
    return target


def _archive_summary(path: Path) -> tuple[int, list[str]]:
    """Return (member count, sorted top-level names). Raises on unreadable."""
    with tarfile.open(path, "r:gz") as tar:
        names = [name for name in tar.getnames() if name != _MANIFEST_NAME]
    tops = sorted({n.split("/", 1)[0] for n in names})
    return len(names), tops


def _running_processes() -> list[str]:
    """Names of live OpenProgram processes that would fight a restore."""
    live: list[str] = []
    try:
        from openprogram.worker.lifecycle import current_worker_pid, find_running_webui

        pid = current_worker_pid()
        if pid is not None:
            live.append(f"worker (PID {pid})")
        else:
            port, _wpid, source = find_running_webui()
            if source == "unmanaged" and port:
                live.append(f"web server (port {port})")
    except Exception:  # pragma: no cover - worker module optional at runtime
        pass
    return live


def _cmd_backup_create(include_credentials: bool = False) -> int:
    try:
        path = create_backup(include_credentials=include_credentials)
    except OSError as exc:
        print(f"[error] backup failed: {exc}", file=sys.stderr)
        return 1

    # Verify readability before reporting success.
    try:
        count, tops = _archive_summary(path)
    except (OSError, tarfile.TarError) as exc:
        print(
            f"[error] archive written but unreadable, removing: {exc}", file=sys.stderr
        )
        path.unlink(missing_ok=True)
        return 1

    size = path.stat().st_size
    print(f"Backup: {path}")
    print(f"  size:    {_human(size)}")
    print(f"  entries: {count} files across {len(tops)} top-level items")
    print(f"  content: {', '.join(tops)}")
    if include_credentials:
        print(
            "  WARNING: credential opt-in allows plaintext credentials from "
            "config, AuthStore, account .env, Channels, and MCP storage."
        )
        print("  Web runtime tokens and pending pairing codes are never included.")
    else:
        print("  credentials excluded (use --include-credentials to include them)")
    return 0


def _list_backups() -> list[Path]:
    try:
        files = sorted(
            backups_dir().glob("*.tar.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    return files


def _cmd_backup_list() -> int:
    files = _list_backups()
    if not files:
        print(f"No backups in {backups_dir()}")
        return 0
    print(f"Backups in {backups_dir()}:")
    for path in files:
        stat = path.stat()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        try:
            _count, tops = _archive_summary(path)
            summary = ", ".join(tops[:6]) + (" ..." if len(tops) > 6 else "")
        except (OSError, tarfile.TarError):
            summary = "(unreadable)"
        print(f"  {path.name}")
        print(f"    {when}  {_human(stat.st_size)}  [{summary}]")
    return 0


def _resolve(name: str) -> Path | None:
    """Accept a bare filename, or an absolute/relative path."""
    candidate = backups_dir() / name
    if candidate.is_file():
        return candidate
    direct = Path(name).expanduser()
    return direct if direct.is_file() else None


def _cmd_backup_restore(
    name: str,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    path = _resolve(name)
    if path is None:
        print(f"[error] no such backup: {name}", file=sys.stderr)
        print(
            "Run `openprogram backup list` to see available archives.", file=sys.stderr
        )
        return 1

    try:
        _count, tops = _archive_summary(path)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] cannot read archive {path}: {exc}", file=sys.stderr)
        return 1

    state = _state_dir()
    if dry_run:
        print(f"Dry run — restoring {path.name} would overwrite, under {state}:")
        for top in tops:
            marker = "overwrite" if (state / top).exists() else "create"
            print(f"  {marker:>9}  {top}")
        return 0

    running = _running_processes()
    if running:
        print(
            "[error] refusing to restore while OpenProgram is running: "
            + ", ".join(running),
            file=sys.stderr,
        )
        print("Stop it first with `openprogram stop`, then retry.", file=sys.stderr)
        return 1

    if not yes:
        print(f"About to restore {path.name} into {state}.")
        print("This overwrites: " + ", ".join(tops))
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Safety net: snapshot current state before overwriting it, so a
    # mistaken restore is itself undoable.
    try:
        safety = create_backup(label="pre-restore")
        print(f"Saved current state to {safety.name} before restoring.")
    except OSError as exc:
        print(
            f"[error] could not back up current state, aborting: {exc}", file=sys.stderr
        )
        return 1

    try:
        with tarfile.open(path, "r:gz") as tar:
            _extract(tar, state)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] restore failed: {exc}", file=sys.stderr)
        print(f"Your previous state is preserved in {safety}", file=sys.stderr)
        return 1

    print(f"Restored {path.name} into {state}")
    print(f"Restored items: {', '.join(tops)}")
    return 0


def _extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract with path containment, refusing anything outside ``dest``."""
    dest = dest.resolve()
    members = tar.getmembers()
    for member in members:
        if member.issym() or member.islnk():
            continue
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest) + os.sep):
            raise tarfile.TarError(f"unsafe path in archive: {member.name}")
    from openprogram.credential_files import (
        _private_atomic_write,
        inventory_for_path,
        preserve_local_secret_bytes,
    )

    for member in members:
        if member.name == _MANIFEST_NAME or member.issym() or member.islnk():
            continue
        target = dest / member.name
        inventory = inventory_for_path(member.name)
        if any(
            entry.whole_file and entry.backup_policy == "never_backup"
            for entry in inventory
        ):
            continue
        if member.isfile() and inventory:
            source = tar.extractfile(member)
            if source is None:
                raise tarfile.TarError(f"cannot read archive member: {member.name}")
            restored = source.read()
            if all(not entry.whole_file for entry in inventory):
                try:
                    local = target.read_bytes()
                except FileNotFoundError:
                    local = None
                restored = preserve_local_secret_bytes(member.name, restored, local)
            _private_atomic_write(
                target,
                lambda handle: handle.write(restored),
                root=dest,
            )
            continue
        try:
            tar.extract(member, dest, filter="data")
        except TypeError:  # pragma: no cover - Python < 3.12
            tar.extract(member, dest)


def _cmd_backup_prune(keep: int) -> int:
    if keep < 1:
        print("[error] --keep must be at least 1", file=sys.stderr)
        return 1
    files = _list_backups()
    doomed = files[keep:]
    if not doomed:
        print(f"Nothing to prune — {len(files)} backup(s), keeping {keep}.")
        return 0
    freed = 0
    for path in doomed:
        try:
            freed += path.stat().st_size
            path.unlink()
            print(f"Removed {path.name}")
        except OSError as exc:
            print(f"[warn] could not remove {path.name}: {exc}", file=sys.stderr)
    print(
        f"Pruned {len(doomed)} backup(s), freed {_human(freed)}. "
        f"{min(keep, len(files))} kept."
    )
    return 0


__all__ = [
    "INCLUDED",
    "CREDENTIAL_ENTRIES",
    "backups_dir",
    "create_backup",
    "_cmd_backup_create",
    "_cmd_backup_list",
    "_cmd_backup_restore",
    "_cmd_backup_prune",
]
