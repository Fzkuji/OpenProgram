"""Create, list, and restore snapshots of the profile state dir.

Scope is an explicit allowlist of top-level entries under the state dir
(``~/.openprogram/`` or ``~/.openprogram-<profile>/``) — the things that
represent *what the user built*: memory workspace, sessions, config,
programs metadata, channel bindings, agents, skills, plugins.

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
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePath

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

# Hardened restore traversal uses POSIX ``dir_fd`` operations when the host
# exposes the complete set. Windows CPython does not, so it takes the explicit
# path-validation fallback below. This is a capability decision rather than a
# platform-name decision: a future runtime that adds the APIs automatically
# receives the stronger descriptor-relative implementation.
_RESTORE_DIR_FD_CAPABLE = all(
    function in os.supports_dir_fd
    for function in (os.open, os.stat, os.unlink, os.replace)
)
_OWNER_FD_CAPABLE = hasattr(os, "geteuid") and hasattr(os, "fchmod")


def _credential_tree_member(relative: str) -> bool:
    parts = PurePath(relative).parts
    return bool(parts) and (
        parts[0] in CREDENTIAL_ENTRIES
        or (len(parts) >= 3 and parts[0] == "profiles" and parts[2] == "auth")
    )


def _state_dir() -> Path:
    from openprogram.paths import get_state_dir

    return get_state_dir()


def _profile_name() -> str:
    from openprogram.paths import get_active_profile

    return get_active_profile() or "default"


def backups_dir() -> Path:
    state = _state_dir()
    state.mkdir(parents=True, exist_ok=True)
    from openprogram.auth.credentials import _ensure_private_directory

    directory = _ensure_private_directory(state / "backups", root=state)
    from openprogram._compat import restrict_directory_to_user

    restrict_directory_to_user(directory)
    return directory


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
    *,
    _lock_held: bool = False,
) -> Path:
    """Write a tar.gz of the in-scope state and return its path.

    Raises OSError if the archive can't be written or read back, or
    RestoreBusyError while a restore owns the profile state lock.
    """
    state = _state_dir()
    if not _lock_held:
        state.mkdir(parents=True, exist_ok=True)
        with _restore_state_lock(state):
            return create_backup(
                include_credentials=include_credentials,
                label=label,
                _lock_held=True,
            )
    out_dir = backups_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = out_dir / f"{_profile_name()}{suffix}-{stamp}.tar.gz"

    from openprogram.auth.credentials import (
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
            or (
                source.is_file()
                and _credential_tree_member(arcname)
                and not inventory_for_path(arcname)
            )
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
    from openprogram._compat import restrict_to_user

    restrict_to_user(target)
    return target


def _archive_summary(path: Path) -> tuple[int, list[str]]:
    """Return (member count, sorted top-level names). Raises on unreadable."""
    with tarfile.open(path, "r:gz") as tar:
        names = [name for name in tar.getnames() if name != _MANIFEST_NAME]
    tops = sorted({n.split("/", 1)[0] for n in names})
    return len(names), tops


def _archive_carries_credentials(path: Path) -> bool:
    """Whether an archive was created with the explicit credential opt-in."""

    try:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.extractfile(_MANIFEST_NAME)
            if member is None:
                return False
            manifest = json.loads(member.read())
    except (OSError, tarfile.TarError, json.JSONDecodeError):
        return False
    return bool(manifest.get("credential_opt_in"))


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


_JOURNAL_NAME = ".restore-journal.json"
_JOURNAL_DIR = ".restore-journal.d"
_RESTORE_LOCK = ".restore.lock"

# Bound at import so the journal keeps working when a test — or a fault
# injector — replaces these on the shared ``os`` module to exercise a
# credential-writer failure. The journal must record that failure, not
# inherit it.
_journal_write = os.write
_journal_fsync = os.fsync
_journal_replace = os.replace
_journal_unlink = os.unlink
_staging_open = os.open
_staging_write = os.write
_staging_fsync = os.fsync


def restore_journal_path(state: Path) -> Path:
    """Where the durable restore journal lives for one state root."""

    return Path(state) / _JOURNAL_NAME


class RestoreBusyError(RuntimeError):
    pass


class RestoreRollbackCompletedError(OSError):
    pass


@contextmanager
def _restore_state_lock(state: Path):
    from openprogram import _compat as file_lock

    state = Path(state)
    lock = state / _RESTORE_LOCK
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("restore lock failed validation")
        if not _OWNER_FD_CAPABLE:
            from openprogram._compat import restrict_to_user

            restrict_to_user(lock)
        else:
            if info.st_uid != os.geteuid():
                raise OSError("restore lock failed validation")
            os.fchmod(descriptor, 0o600)
        try:
            file_lock.flock(descriptor, file_lock.LOCK_EX | file_lock.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise RestoreBusyError("another restore owns the state lock") from exc
        try:
            yield
        finally:
            file_lock.flock(descriptor, file_lock.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def _restore_target_locks(state: Path, relative_paths: Iterable[str]):
    """Hold target writer locks in one stable order for a restore transaction."""

    from openprogram.auth.credentials import _private_file_lock

    with ExitStack() as stack:
        for relative in sorted(set(relative_paths)):
            stack.enter_context(_private_file_lock(state / relative, root=state))
        yield


class _RestoreJournal:
    """Durable record of what a restore replaced, for reversal.

    Written before the first publish and fsynced after every entry, so a
    process killed at any point leaves enough on disk to put the old
    state back. Old copies live beside it under the same state root, so
    reversal is a same-filesystem rename.
    """

    def __init__(self, state: Path) -> None:
        self.state = Path(state)
        self.path = restore_journal_path(self.state)
        self.backup_dir = self.state / _JOURNAL_DIR
        self.entries: list[dict] = []
        self._backup_fd: int | None = None

    def start(self) -> None:
        try:
            os.mkdir(self.backup_dir, 0o700)
        except FileExistsError as exc:
            raise OSError("restore journal directory already exists") from exc
        if not _RESTORE_DIR_FD_CAPABLE:
            from openprogram._compat import restrict_directory_to_user

            _validate_fallback_path(self.state, self.backup_dir, directory=True)
            restrict_directory_to_user(self.backup_dir)
            self._flush(complete=False)
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        self._backup_fd = os.open(self.backup_dir, flags)
        try:
            info = os.fstat(self._backup_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise OSError("restore journal directory failed validation")
            os.fchmod(self._backup_fd, 0o700)
            self._flush(complete=False)
        except BaseException:
            self._close_backup_fd()
            raise

    def record(self, relative: str, previous: Path | None) -> None:
        self.entries.append(
            {
                "relative_path": relative,
                "previous": (
                    previous.relative_to(self.state).as_posix() if previous else None
                ),
                "existed": previous is not None,
            }
        )
        self._flush(complete=False)

    def preserve(self, relative: str, target: Path) -> Path | None:
        """Copy the current bytes aside so the publish can be reversed."""

        if not _RESTORE_DIR_FD_CAPABLE:
            return self._preserve_without_dir_fd(relative, target)
        if self._backup_fd is None:
            raise OSError("restore journal has not started")
        target_parts = PurePath(relative).parts
        root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        root_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        root = os.open(self.state, root_flags)
        parent = -1
        try:
            _verify_recovery_directory(root)
            parent = _open_recovery_directory(root, target_parts[:-1])
            try:
                before = os.stat(
                    target_parts[-1], dir_fd=parent, follow_symlinks=False
                )
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise OSError("restore target is not a regular file")
            source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            source_flags |= getattr(os, "O_CLOEXEC", 0)
            source = os.open(target_parts[-1], source_flags, dir_fd=parent)
        finally:
            os.close(root)
            if parent >= 0:
                os.close(parent)
        name = f"{len(self.entries):08d}.previous"
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        destination = -1
        try:
            opened = os.fstat(source)
            if (
                (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
            ):
                raise OSError("restore source failed validation")
            destination = os.open(
                name, destination_flags, 0o600, dir_fd=self._backup_fd
            )
            while payload := os.read(source, 1024 * 1024):
                written = 0
                while written < len(payload):
                    count = _journal_write(destination, payload[written:])
                    if count <= 0:
                        raise OSError("restore preserve write made no progress")
                    written += count
            _journal_fsync(destination)
        finally:
            os.close(source)
            if destination >= 0:
                os.close(destination)
        return self.backup_dir / name

    def _preserve_without_dir_fd(self, relative: str, target: Path) -> Path | None:
        """Path-based preserve with reparse-point validation.

        CPython does not expose ``dir_fd`` traversal on Windows.  Validate
        every existing component before opening the file, then keep the
        rollback copy inside the private journal directory and open it with
        ``O_EXCL`` so an existing path is never followed or overwritten.
        """

        del relative
        try:
            _validate_fallback_path(self.state, target, regular=True)
        except FileNotFoundError:
            return None
        source = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        name = f"{len(self.entries):08d}.previous"
        previous = self.backup_dir / name
        destination = -1
        try:
            destination = os.open(
                previous,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            while payload := os.read(source, 1024 * 1024):
                written = 0
                while written < len(payload):
                    count = _journal_write(destination, payload[written:])
                    if count <= 0:
                        raise OSError("restore preserve write made no progress")
                    written += count
            _journal_fsync(destination)
        finally:
            os.close(source)
            if destination >= 0:
                os.close(destination)
        from openprogram._compat import restrict_to_user

        restrict_to_user(previous)
        return previous

    def finish(self) -> None:
        from openprogram.auth.credentials import _read_private_bytes, inventory_for_path

        for entry in self.entries:
            relative = entry["relative_path"]
            if inventory_for_path(relative):
                _read_private_bytes(self.state / relative, root=self.state)
        self._flush(complete=True)
        self.discard()

    def discard(self) -> None:
        self._close_backup_fd()
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        self.path.unlink(missing_ok=True)

    def _close_backup_fd(self) -> None:
        if self._backup_fd is not None:
            os.close(self._backup_fd)
            self._backup_fd = None

    def _flush(self, *, complete: bool) -> None:
        payload = json.dumps(
            {
                "format_version": 1,
                "complete": complete,
                "entries": self.entries,
            },
            indent=2,
        ).encode()
        # Deliberately not the credential writer: the journal is the thing
        # that must survive a failing credential publish, so it cannot share
        # the code path whose failure it exists to record.
        try:
            live = os.lstat(self.path)
        except FileNotFoundError:
            live = None
        if live is not None and (
            stat.S_ISLNK(live.st_mode) or not stat.S_ISREG(live.st_mode)
        ):
            raise OSError("restore journal is not a regular file")
        temporary = self.state / f".{_JOURNAL_NAME}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            try:
                written = 0
                while written < len(payload):
                    count = _journal_write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("restore journal write made no progress")
                    written += count
                _journal_fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                live = os.lstat(self.path)
            except FileNotFoundError:
                live = None
            if live is not None and (
                stat.S_ISLNK(live.st_mode) or not stat.S_ISREG(live.st_mode)
            ):
                raise OSError("restore journal is not a regular file")
            _journal_replace(temporary, self.path)
            if _RESTORE_DIR_FD_CAPABLE:
                directory = os.open(
                    self.state,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
                )
                try:
                    _journal_fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


class UnrecoverableRestoreJournalError(OSError):
    pass


def recover_interrupted_restore(state: Path, *, _lock_held: bool = False) -> bool:
    """Reverse a restore that died mid-flight. Returns whether it acted.

    Safe to call repeatedly: a journal marked complete, or none at all,
    means the last restore either finished or never published, so there
    is nothing to undo.
    """

    state = Path(state)
    if not _lock_held:
        with _restore_state_lock(state):
            return recover_interrupted_restore(state, _lock_held=True)
    journal_file = restore_journal_path(state)
    try:
        before = os.lstat(journal_file)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise UnrecoverableRestoreJournalError("restore journal is unsafe")
        descriptor = os.open(
            journal_file,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise UnrecoverableRestoreJournalError("restore journal changed")
            chunks = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        record = json.loads(b"".join(chunks))
    except FileNotFoundError:
        return False
    except UnrecoverableRestoreJournalError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise UnrecoverableRestoreJournalError(
            "restore journal cannot be safely read"
        ) from exc

    validated = _validate_restore_journal(record)
    if validated is None:
        raise UnrecoverableRestoreJournalError("restore journal schema is invalid")
    complete, entries = validated
    if complete:
        shutil.rmtree(state / _JOURNAL_DIR, ignore_errors=True)
        journal_file.unlink(missing_ok=True)
        return False

    from openprogram.auth.credentials import PrivateAtomicWriteError

    try:
        with _restore_target_locks(
            state, (entry["relative_path"] for entry in entries)
        ):
            _reverse(state, entries)
    except PrivateAtomicWriteError as exc:
        if exc.code == "lock_timeout":
            raise
        raise UnrecoverableRestoreJournalError(
            "restore journal paths are unsafe"
        ) from None
    except _UnsafeRecoveryPath as exc:
        raise UnrecoverableRestoreJournalError(
            "restore journal paths are unsafe"
        ) from exc
    shutil.rmtree(state / _JOURNAL_DIR, ignore_errors=True)
    journal_file.unlink(missing_ok=True)
    return True


def _validate_restore_journal(record: object) -> tuple[bool, list[dict]] | None:
    if not isinstance(record, dict) or record.get("format_version") != 1:
        return None
    if isinstance(record.get("format_version"), bool):
        return None
    complete = record.get("complete")
    entries = record.get("entries")
    if not isinstance(complete, bool) or not isinstance(entries, list):
        return None
    validated: list[dict] = []
    relative_paths: set[str] = set()
    previous_paths: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
            "relative_path",
            "previous",
            "existed",
        }:
            return None
        relative = entry["relative_path"]
        existed = entry["existed"]
        previous = entry["previous"]
        if not _safe_relative_path(relative) or not isinstance(existed, bool):
            return None
        if relative in relative_paths:
            return None
        relative_paths.add(relative)
        if existed:
            if not isinstance(previous, str) or not _safe_relative_path(previous):
                return None
            if PurePath(previous).parts[0] != _JOURNAL_DIR:
                return None
            expected_previous = f"{_JOURNAL_DIR}/{index:08d}.previous"
            if previous != expected_previous or previous in previous_paths:
                return None
            previous_paths.add(previous)
        elif previous is not None:
            return None
        validated.append(entry)
    return complete, validated


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePath(value)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


class _UnsafeRecoveryPath(Exception):
    pass


def _reverse(state: Path, entries: list[dict]) -> None:
    if not _RESTORE_DIR_FD_CAPABLE:
        _reverse_without_dir_fd(state, entries)
        return
    prepared = _prepare_recovery_entries(state, entries)
    try:
        for target_parent, target_name, existed, source in reversed(prepared):
            if existed:
                assert source is not None
                _restore_opened_source(source, target_parent, target_name)
            else:
                try:
                    os.unlink(target_name, dir_fd=target_parent)
                except FileNotFoundError:
                    pass
                _journal_fsync(target_parent)
    finally:
        for target_parent, _target_name, _existed, source in prepared:
            os.close(target_parent)
            if source is not None:
                os.close(source)


def _reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _validate_fallback_path(
    root: Path,
    path: Path,
    *,
    regular: bool = False,
    directory: bool = False,
) -> os.stat_result:
    """Validate an existing path below ``root`` without following
    symlinks, junctions, or other reparse points.

    Windows has no Python ``dir_fd`` API, so this is the platform-specific
    containment contract used by restore journalling and rollback.
    """

    root = Path(root).resolve()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise _UnsafeRecoveryPath from exc
    current = root
    root_info = os.lstat(current)
    if _reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise _UnsafeRecoveryPath
    for index, part in enumerate(relative.parts):
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or _reparse_point(info):
            raise _UnsafeRecoveryPath
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise _UnsafeRecoveryPath
    info = os.lstat(candidate)
    if regular and not stat.S_ISREG(info.st_mode):
        raise _UnsafeRecoveryPath
    if directory and not stat.S_ISDIR(info.st_mode):
        raise _UnsafeRecoveryPath
    return info


def _reverse_without_dir_fd(state: Path, entries: list[dict]) -> None:
    """Reverse a restore using validated path operations.

    All paths come from the already schema-validated journal.  Existing
    components are revalidated immediately before each operation so a
    junction or symlink cannot redirect recovery outside the state root.
    """

    state = Path(state).resolve()
    prepared: list[tuple[Path, bool, Path | None]] = []
    try:
        for entry in entries:
            target = state / Path(entry["relative_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            _validate_fallback_path(state, target.parent, directory=True)
            if target.exists() or target.is_symlink():
                _validate_fallback_path(state, target, regular=True)
            previous = None
            if entry["existed"]:
                previous = state / Path(entry["previous"])
                _validate_fallback_path(state, previous, regular=True)
            prepared.append((target, entry["existed"], previous))
    except (OSError, _UnsafeRecoveryPath) as exc:
        raise _UnsafeRecoveryPath from exc

    for target, existed, previous in reversed(prepared):
        if existed:
            assert previous is not None
            _restore_path_source(previous, target, root=state)
        else:
            try:
                _validate_fallback_path(state, target, regular=True)
                _journal_unlink(target)
            except FileNotFoundError:
                pass


def _restore_path_source(source: Path, target: Path, *, root: Path) -> None:
    """Restore one fallback rollback copy without the public credential writer.

    The journal primitives are bound at import time so rollback still works
    when a writer fault injector (or a real broken writer dependency) replaces
    ``os.write``, ``os.fsync``, or ``os.replace`` on the shared module.
    """

    _validate_fallback_path(root, source, regular=True)
    _validate_fallback_path(root, target.parent, directory=True)
    temporary = target.parent / f".{target.name}.{secrets.token_hex(12)}.rollback"
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    target_fd = -1
    published = False
    try:
        target_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        while payload := os.read(source_fd, 1024 * 1024):
            written = 0
            while written < len(payload):
                count = _journal_write(target_fd, payload[written:])
                if count <= 0:
                    raise OSError("restore rollback write made no progress")
                written += count
        _journal_fsync(target_fd)
        os.close(target_fd)
        target_fd = -1
        _journal_replace(temporary, target)
        published = True
        from openprogram._compat import restrict_to_user

        restrict_to_user(target)
    finally:
        os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)
        if not published:
            try:
                _journal_unlink(temporary)
            except FileNotFoundError:
                pass


def _prepare_recovery_entries(
    state: Path, entries: list[dict]
) -> list[tuple[int, str, bool, int | None]]:
    prepared: list[tuple[int, str, bool, int | None]] = []
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    root_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        root = os.open(state, root_flags)
        _verify_recovery_directory(root)
        for entry in entries:
            target_parts = PurePath(entry["relative_path"]).parts
            target_parent = _open_recovery_directory(root, target_parts[:-1])
            source = None
            try:
                _verify_recovery_target_at(
                    target_parent, target_parts[-1], entry["existed"]
                )
                if entry["existed"]:
                    source_parts = PurePath(entry["previous"]).parts
                    source_parent = _open_recovery_directory(root, source_parts[:-1])
                    try:
                        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        flags |= getattr(os, "O_CLOEXEC", 0)
                        source = os.open(
                            source_parts[-1], flags, dir_fd=source_parent
                        )
                        info = os.fstat(source)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_uid != os.geteuid()
                            or stat.S_IMODE(info.st_mode) != 0o600
                        ):
                            raise _UnsafeRecoveryPath
                    finally:
                        os.close(source_parent)
                prepared.append(
                    (target_parent, target_parts[-1], entry["existed"], source)
                )
            except BaseException:
                os.close(target_parent)
                if source is not None:
                    os.close(source)
                raise
    except (OSError, _UnsafeRecoveryPath) as exc:
        for target_parent, _target_name, _existed, source in prepared:
            os.close(target_parent)
            if source is not None:
                os.close(source)
        raise _UnsafeRecoveryPath from exc
    finally:
        if "root" in locals():
            os.close(root)
    return prepared


def _open_recovery_directory(root: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        for part in parts:
            following = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = following
            _verify_recovery_directory(current)
        return current
    except BaseException:
        os.close(current)
        raise


def _verify_recovery_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise _UnsafeRecoveryPath


def _verify_recovery_target_at(parent: int, name: str, existed: bool) -> None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        if existed:
            raise _UnsafeRecoveryPath
        return
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
    ):
        raise _UnsafeRecoveryPath


def _restore_opened_source(source: int, parent: int, target_name: str) -> None:
    temporary = f".{target_name}.{secrets.token_hex(12)}.rollback"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
    published = False
    try:
        os.lseek(source, 0, os.SEEK_SET)
        while payload := os.read(source, 1024 * 1024):
            written = 0
            while written < len(payload):
                count = _journal_write(descriptor, payload[written:])
                if count <= 0:
                    raise OSError("restore rollback write made no progress")
                written += count
        _journal_fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _journal_replace(
            temporary,
            target_name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        published = True
        _journal_fsync(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _publish_restored(target: Path, payload: bytes, *, root: Path) -> None:
    """Publish one validated member through the shared private writer."""

    from openprogram.auth.credentials import _private_atomic_write

    _private_atomic_write(target, lambda handle: handle.write(payload), root=root)


def restore_archive(
    archive: Path, state: Path, *, _lock_held: bool = False
) -> list[str]:
    """Validate an archive completely, then publish it or change nothing.

    Every member is extracted into a staging directory beside the state
    root — same filesystem, so publication is a rename — and validated
    there: containment, member type, registered-secret JSON shape, and
    manifest presence. Only once the whole archive passes does anything
    become visible, and each publish is journalled so a mid-restore
    failure reverses the targets already written.
    """

    from openprogram.auth.credentials import (
        _read_private_bytes,
        backup_bytes,
        inventory_for_path,
        preserve_local_secret_bytes,
    )

    state = Path(state).resolve()
    if not _lock_held:
        with _restore_state_lock(state):
            return restore_archive(archive, state, _lock_held=True)
    recover_interrupted_restore(state, _lock_held=True)

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        member_names = [member.name for member in members]
        if len(member_names) != len(set(member_names)):
            raise tarfile.TarError("duplicate member name in archive")
        names = set(member_names)
        if _MANIFEST_NAME not in names:
            raise tarfile.TarError("archive has no manifest; refusing to restore")
        manifest_source = tar.extractfile(_MANIFEST_NAME)
        if manifest_source is None:
            raise tarfile.TarError("archive manifest cannot be read")
        try:
            manifest = json.loads(manifest_source.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise tarfile.TarError("archive manifest is not valid JSON") from exc
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("format_version")) is not int
            or manifest.get("format_version") != 1
            or not isinstance(manifest.get("credential_opt_in"), bool)
        ):
            raise tarfile.TarError("archive manifest has an unsupported schema")
        credential_opt_in = manifest["credential_opt_in"]

        staged: list[tuple[str, bytes, tuple]] = []
        for member in members:
            if member.name == _MANIFEST_NAME:
                continue
            if member.issym() or member.islnk():
                raise tarfile.TarError(f"link member in archive: {member.name}")
            target = (state / member.name).resolve()
            if not str(target).startswith(str(state) + os.sep):
                raise tarfile.TarError(f"unsafe path in archive: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise tarfile.TarError(
                    f"unsupported member type in archive: {member.name}"
                )

            inventory = inventory_for_path(member.name)
            if _credential_tree_member(member.name) and not inventory:
                raise tarfile.TarError(
                    f"credential inventory does not recognize: {member.name}"
                )
            if any(
                entry.whole_file and entry.backup_policy == "never_backup"
                for entry in inventory
            ):
                continue
            source = tar.extractfile(member)
            if source is None:
                raise tarfile.TarError(f"cannot read archive member: {member.name}")
            payload = source.read()
            if inventory:
                if not credential_opt_in:
                    safe_payload = backup_bytes(member.name, payload, include_credentials=False)
                    if safe_payload is None:
                        raise tarfile.TarError(
                            "credential_opt_in does not authorize secret member: "
                            f"{member.name}"
                        )
                    try:
                        original_value = json.loads(payload)
                        safe_value = json.loads(safe_payload)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        original_value = payload
                        safe_value = safe_payload
                    if original_value != safe_value and _has_secret_material(
                        original_value, safe_value
                    ):
                        raise tarfile.TarError(
                            "credential_opt_in does not authorize secret fields: "
                            f"{member.name}"
                        )
                # A registered JSON secret file must parse before
                # publication: publishing unparseable bytes over a live
                # credential turns a corrupt archive into a lost one.
                # Line-oriented members (a profile ``.env``) have no JSON
                # shape to check, so only their containment and type gate.
                if member.name.endswith(".json"):
                    try:
                        json.loads(payload)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise tarfile.TarError(
                            f"registered secret member is not valid JSON: "
                            f"{member.name}"
                        ) from exc
            staged.append((member.name, payload, inventory))

    staging = Path(tempfile.mkdtemp(prefix=".restore-staging-", dir=state.parent))
    try:
        from openprogram._compat import restrict_directory_to_user

        restrict_directory_to_user(staging)
        if os.stat(staging).st_dev != os.stat(state).st_dev:
            raise OSError("restore staging is not on the state filesystem")
        staged_files: list[tuple[str, Path, tuple]] = []
        for index, (relative, payload, inventory) in enumerate(staged):
            staged_file = staging / f"{index:08d}.payload"
            descriptor = _staging_open(
                staged_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                written = 0
                while written < len(payload):
                    count = _staging_write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("restore staging write made no progress")
                    written += count
                _staging_fsync(descriptor)
            finally:
                os.close(descriptor)
            staged_files.append((relative, staged_file, inventory))
        if _RESTORE_DIR_FD_CAPABLE:
            directory = _staging_open(
                staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                _staging_fsync(directory)
            finally:
                os.close(directory)

        with _restore_target_locks(
            state, (relative for relative, _path, _inventory in staged_files)
        ):
            journal = _RestoreJournal(state)
            journal.start()
            published: list[str] = []
            try:
                for relative, staged_file, inventory in staged_files:
                    payload = staged_file.read_bytes()
                    target = state / relative
                    from openprogram.auth.credentials import _ensure_private_directory

                    _ensure_private_directory(target.parent, root=state)
                    if inventory and all(not entry.whole_file for entry in inventory):
                        payload = preserve_local_secret_bytes(
                            relative,
                            payload,
                            _read_private_bytes(target, root=state),
                        )
                    journal.record(relative, journal.preserve(relative, target))
                    _publish_restored(target, payload, root=state)
                    published.append(relative)
                for relative, _staged_file, expected_inventory in staged_files:
                    if not expected_inventory:
                        continue
                    if inventory_for_path(relative) != expected_inventory:
                        raise OSError(
                            f"credential inventory changed during restore: {relative}"
                        )
                    _read_private_bytes(state / relative, root=state)
                journal.finish()
            except BaseException as exc:
                _reverse(state, journal.entries)
                journal.discard()
                if not isinstance(exc, Exception):
                    raise
                raise RestoreRollbackCompletedError(
                    "publication failed; rollback complete"
                ) from exc
        return published
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _has_secret_material(original: object, redacted: object) -> bool:
    """Whether redaction removed a non-empty value from a mixed file."""

    if isinstance(original, dict) and isinstance(redacted, dict):
        for key, value in original.items():
            if key not in redacted:
                if _nonempty_secret_value(value):
                    return True
            elif _has_secret_material(value, redacted[key]):
                return True
        return False
    return False


def _nonempty_secret_value(value: object) -> bool:
    if isinstance(value, dict):
        return any(_nonempty_secret_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_nonempty_secret_value(item) for item in value)
    return value not in (None, "", False)


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
    "UnrecoverableRestoreJournalError",
    "RestoreBusyError",
    "RestoreRollbackCompletedError",
    "backups_dir",
    "create_backup",
    "recover_interrupted_restore",
    "restore_archive",
    "restore_journal_path",
    "_cmd_backup_create",
    "_cmd_backup_list",
    "_cmd_backup_restore",
    "_cmd_backup_prune",
]
