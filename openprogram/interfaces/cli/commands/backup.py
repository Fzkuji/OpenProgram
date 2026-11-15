"""``openprogram backup`` — create, list, restore and prune state snapshots.

The snapshot engine (scope, archive format, staged restore and its journal)
is :mod:`openprogram.operations.backup`; this module is the terminal command
that calls it and prints the result.
"""
from __future__ import annotations

import logging
import tarfile
import sys
import time
from pathlib import Path

from openprogram.operations.backup import (
    RestoreBusyError,
    RestoreRollbackCompletedError,
    UnrecoverableRestoreJournalError,
    _archive_carries_credentials,
    _archive_summary,
    _restore_state_lock,
    _state_dir,
    backups_dir,
    create_backup,
    restore_archive,
)

_log = logging.getLogger(__name__)


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _running_processes() -> list[str]:
    """Names of live OpenProgram processes that would fight a restore."""
    live: list[str] = []
    try:
        from openprogram.operations.worker.lifecycle import current_worker_pid, find_running_webui

        pid = current_worker_pid()
        if pid is not None:
            live.append(f"worker (PID {pid})")
        else:
            port, _wpid, source = find_running_webui()
            if source == "unmanaged" and port:
                live.append(f"web server (port {port})")
    except Exception:  # pragma: no cover - worker module optional at runtime
        # The liveness check only adds a warning; backup proceeds without it.
        _log.debug("live process check failed", exc_info=True)
    return live


def _cmd_backup_create(include_credentials: bool = False) -> int:
    try:
        path = create_backup(include_credentials=include_credentials)
    except RestoreBusyError:
        print("[error] another restore is already in progress", file=sys.stderr)
        return 1
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
    # A credential-bearing archive is about to overwrite live credentials,
    # so the same explicit authorization that produced it lets the safety
    # snapshot keep them too — otherwise the undo would silently drop the
    # very secrets the restore replaced.
    snapshot_credentials = _archive_carries_credentials(path)
    try:
        with _restore_state_lock(state):
            try:
                safety = create_backup(
                    include_credentials=snapshot_credentials,
                    label="pre-restore",
                    _lock_held=True,
                )
                print(f"Saved current state to {safety.name} before restoring.")
                if snapshot_credentials:
                    print(
                        "That snapshot contains plaintext credentials, because the "
                        "archive you are restoring does."
                    )
            except OSError as exc:
                print(
                    f"[error] could not back up current state, aborting: {exc}",
                    file=sys.stderr,
                )
                return 1

            try:
                restore_archive(path, state, _lock_held=True)
            except UnrecoverableRestoreJournalError:
                print(
                    "[error] restore blocked by an unrecoverable restore journal",
                    file=sys.stderr,
                )
                print(
                    "The previous state could not be verified as restored.",
                    file=sys.stderr,
                )
                print(f"A snapshot is preserved in {safety}", file=sys.stderr)
                return 1
            except RestoreRollbackCompletedError as exc:
                print(f"[error] restore failed: {exc}", file=sys.stderr)
                print("Your previous state was restored in place.", file=sys.stderr)
                print(f"A snapshot is also preserved in {safety}", file=sys.stderr)
                return 1
            except (OSError, tarfile.TarError) as exc:
                print(f"[error] restore failed: {exc}", file=sys.stderr)
                print(
                    "The previous state could not be verified as restored.",
                    file=sys.stderr,
                )
                print(f"A snapshot is also preserved in {safety}", file=sys.stderr)
                return 1
    except RestoreBusyError:
        print("[error] another restore is already in progress", file=sys.stderr)
        print("No restore changes were made by this command.", file=sys.stderr)
        return 1

    print(f"Restored {path.name} into {state}")
    print(f"Restored items: {', '.join(tops)}")
    return 0


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
    "_cmd_backup_create",
    "_cmd_backup_list",
    "_cmd_backup_restore",
    "_cmd_backup_prune",
]
