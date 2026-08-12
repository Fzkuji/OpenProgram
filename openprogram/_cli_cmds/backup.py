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
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile
import time
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
        safety = create_backup(
            include_credentials=snapshot_credentials, label="pre-restore"
        )
        print(f"Saved current state to {safety.name} before restoring.")
        if snapshot_credentials:
            print(
                "That snapshot contains plaintext credentials, because the "
                "archive you are restoring does."
            )
    except OSError as exc:
        print(
            f"[error] could not back up current state, aborting: {exc}", file=sys.stderr
        )
        return 1

    try:
        restore_archive(path, state)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] restore failed: {exc}", file=sys.stderr)
        print("Your previous state was restored in place.", file=sys.stderr)
        print(f"A snapshot is also preserved in {safety}", file=sys.stderr)
        return 1

    print(f"Restored {path.name} into {state}")
    print(f"Restored items: {', '.join(tops)}")
    return 0


_JOURNAL_NAME = ".restore-journal.json"
_JOURNAL_DIR = ".restore-journal.d"

# Bound at import so the journal keeps working when a test — or a fault
# injector — replaces these on the shared ``os`` module to exercise a
# credential-writer failure. The journal must record that failure, not
# inherit it.
_journal_write = os.write
_journal_fsync = os.fsync
_journal_replace = os.replace
_staging_open = os.open
_staging_write = os.write
_staging_fsync = os.fsync


def restore_journal_path(state: Path) -> Path:
    """Where the durable restore journal lives for one state root."""

    return Path(state) / _JOURNAL_NAME


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

    def start(self) -> None:
        try:
            info = os.lstat(self.backup_dir)
        except FileNotFoundError:
            self.backup_dir.mkdir(mode=0o700)
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError("restore journal directory is not a real directory")
            if info.st_uid != os.geteuid():
                raise OSError("restore journal directory has a foreign owner")
            os.chmod(self.backup_dir, 0o700)
        self._flush(complete=False)

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

        try:
            info = os.lstat(target)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError("restore target is not a regular file")
        keep = self.backup_dir / f"{len(self.entries):08d}.previous"
        keep.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(target, keep)
        os.chmod(keep, 0o600)
        return keep

    def finish(self) -> None:
        self._flush(complete=True)
        self.discard()

    def discard(self) -> None:
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        self.path.unlink(missing_ok=True)

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


def recover_interrupted_restore(state: Path) -> bool:
    """Reverse a restore that died mid-flight. Returns whether it acted.

    Safe to call repeatedly: a journal marked complete, or none at all,
    means the last restore either finished or never published, so there
    is nothing to undo.
    """

    state = Path(state)
    journal_file = restore_journal_path(state)
    try:
        before = os.lstat(journal_file)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return False
        descriptor = os.open(
            journal_file,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                return False
            chunks = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        record = json.loads(b"".join(chunks))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError):
        # An unreadable journal cannot direct a rollback; leave it for
        # the operator rather than guessing at the old state.
        return False

    validated = _validate_restore_journal(record)
    if validated is None:
        return False
    complete, entries = validated
    if complete:
        shutil.rmtree(state / _JOURNAL_DIR, ignore_errors=True)
        journal_file.unlink(missing_ok=True)
        return False

    if not _validate_recovery_paths(state, entries):
        return False
    _reverse(state, entries)
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


def _reverse(state: Path, entries: list[dict]) -> None:
    for entry in reversed(entries):
        relative = entry.get("relative_path")
        if not isinstance(relative, str) or not relative:
            continue
        target = state / relative
        previous = entry.get("previous")
        if entry.get("existed") and isinstance(previous, str):
            source = state / previous
            if source.exists():
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _journal_replace(source, target)
                os.chmod(target, 0o600)
        else:
            target.unlink(missing_ok=True)


def _validate_recovery_paths(state: Path, entries: list[dict]) -> bool:
    """Validate every rollback path before the first filesystem mutation."""

    try:
        root = os.lstat(state)
        if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode):
            return False
        if root.st_uid != os.geteuid():
            return False
        for entry in entries:
            target = state / entry["relative_path"]
            if not _validate_recovery_target(state, target, entry["existed"]):
                return False
            if entry["existed"]:
                source = state / entry["previous"]
                if not _validate_recovery_source(state, source):
                    return False
    except OSError:
        return False
    return True


def _validate_recovery_target(state: Path, target: Path, existed: bool) -> bool:
    current = state
    relative = target.relative_to(state)
    for part in relative.parts[:-1]:
        current /= part
        info = os.lstat(current)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
        ):
            return False
    try:
        final = os.lstat(target)
    except FileNotFoundError:
        return not existed
    return (
        not stat.S_ISLNK(final.st_mode)
        and stat.S_ISREG(final.st_mode)
        and final.st_uid == os.geteuid()
    )


def _validate_recovery_source(state: Path, source: Path) -> bool:
    current = state
    relative = source.relative_to(state)
    for part in relative.parts[:-1]:
        current /= part
        info = os.lstat(current)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
        ):
            return False
    info = os.lstat(source)
    return (
        not stat.S_ISLNK(info.st_mode)
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600
    )


def _publish_restored(target: Path, payload: bytes, *, root: Path) -> None:
    """Publish one validated member through the shared private writer."""

    from openprogram.credential_files import _private_atomic_write

    _private_atomic_write(target, lambda handle: handle.write(payload), root=root)


def restore_archive(archive: Path, state: Path) -> list[str]:
    """Validate an archive completely, then publish it or change nothing.

    Every member is extracted into a staging directory beside the state
    root — same filesystem, so publication is a rename — and validated
    there: containment, member type, registered-secret JSON shape, and
    manifest presence. Only once the whole archive passes does anything
    become visible, and each publish is journalled so a mid-restore
    failure reverses the targets already written.
    """

    from openprogram.credential_files import (
        backup_bytes,
        inventory_for_path,
        preserve_local_secret_bytes,
    )

    state = Path(state).resolve()
    recover_interrupted_restore(state)

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

        staged: list[tuple[str, bytes]] = []
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
                if all(not entry.whole_file for entry in inventory):
                    try:
                        local = (state / member.name).read_bytes()
                    except (FileNotFoundError, NotADirectoryError):
                        local = None
                    payload = preserve_local_secret_bytes(
                        member.name, payload, local
                    )
            staged.append((member.name, payload))

    staging = Path(tempfile.mkdtemp(prefix=".restore-staging-", dir=state.parent))
    try:
        os.chmod(staging, 0o700)
        if os.stat(staging).st_dev != os.stat(state).st_dev:
            raise OSError("restore staging is not on the state filesystem")
        staged_files: list[tuple[str, Path]] = []
        for index, (relative, payload) in enumerate(staged):
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
            staged_files.append((relative, staged_file))
        directory = _staging_open(
            staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            _staging_fsync(directory)
        finally:
            os.close(directory)

        journal = _RestoreJournal(state)
        journal.start()
        published: list[str] = []
        try:
            for relative, staged_file in staged_files:
                payload = staged_file.read_bytes()
                target = state / relative
                from openprogram.credential_files import _ensure_private_directory

                _ensure_private_directory(target.parent, root=state)
                journal.record(relative, journal.preserve(relative, target))
                _publish_restored(target, payload, root=state)
                published.append(relative)
        except BaseException:
            if not _validate_recovery_paths(state, journal.entries):
                raise OSError("restore rollback paths failed validation")
            _reverse(state, journal.entries)
            journal.discard()
            raise
        journal.finish()
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
