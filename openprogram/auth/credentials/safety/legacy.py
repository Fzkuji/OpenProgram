"""Inventory and low-level file mechanics for OpenProgram-owned secrets.

The inventory is data, not a second storage layer.  It describes the existing
plaintext files so backup, restore, and later writer/doctor work use the same
classification.  This module does not read environment secrets or invoke
external credential helpers.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import secrets
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator, Literal

SecretLifecycle = Literal["persistent", "ephemeral"]
BackupPolicy = Literal[
    "redact_default",
    "include_on_opt_in",
    "never_backup",
]
DeleteAction = Literal["clear_field", "unlink"]


@dataclass(frozen=True)
class SecretInventoryEntry:
    """One current credential file or mixed-file field surface."""

    kind: str
    path_pattern: str
    secret_fields: tuple[str, ...]
    writer: str
    lifecycle: SecretLifecycle
    backup_policy: BackupPolicy
    delete_action: DeleteAction

    @property
    def whole_file(self) -> bool:
        return self.secret_fields == ("$",)

    def matches(self, relative_path: str | PurePosixPath) -> bool:
        normalized = PurePosixPath(relative_path).as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        path_parts = PurePosixPath(normalized).parts
        pattern_parts = PurePosixPath(self.path_pattern).parts
        return len(path_parts) == len(pattern_parts) and all(
            fnmatch.fnmatchcase(part, pattern)
            for part, pattern in zip(path_parts, pattern_parts)
        )


SECRET_INVENTORY: tuple[SecretInventoryEntry, ...] = (
    SecretInventoryEntry(
        "config_api_keys",
        "config.json",
        ("api_keys",),
        "openprogram.setup._write_config",
        "persistent",
        "redact_default",
        "clear_field",
    ),
    SecretInventoryEntry(
        "auth_store",
        "auth/*/*.json",
        ("$",),
        "openprogram.auth.store.AuthStore",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "profile_auth_store",
        "profiles/*/auth/*/*.json",
        ("$",),
        "openprogram.auth.store.AuthStore",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "profile_env",
        "profiles/*/.env",
        ("$",),
        "openprogram.auth.accounts._write_dotenv",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "channel_credentials",
        "channels/*/accounts/*/credentials.json",
        ("$",),
        "openprogram.channels.accounts.save_credentials",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "channel_pairing_codes",
        "channels/*/accounts/*/access.json",
        ("pending",),
        "openprogram.channels._access._save",
        "ephemeral",
        "never_backup",
        "clear_field",
    ),
    SecretInventoryEntry(
        "mcp_server_secrets",
        "mcp_servers.json",
        (
            "servers.*.env",
            "servers.*.headers",
            "servers.*.auth.token",
            "servers.*.auth.client_secret",
        ),
        "openprogram.mcp.config.save_configs",
        "persistent",
        "redact_default",
        "clear_field",
    ),
    SecretInventoryEntry(
        "mcp_tokens",
        "mcp_tokens/*.json",
        ("$",),
        "openprogram.mcp.token_storage.FileTokenStorage",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "web_runtime_token",
        "web/token",
        ("$",),
        "openprogram.webui.owner_auth._write_private_text",
        "ephemeral",
        "never_backup",
        "unlink",
    ),
)


def inventory_for_path(
    relative_path: str | PurePosixPath,
) -> tuple[SecretInventoryEntry, ...]:
    """Return all inventory rows matching one state-relative path."""

    return tuple(entry for entry in SECRET_INVENTORY if entry.matches(relative_path))


def backup_bytes(
    relative_path: str | PurePosixPath,
    raw: bytes,
    *,
    include_credentials: bool,
) -> bytes | None:
    """Return safe archive bytes, or ``None`` to exclude the whole file."""

    entries = inventory_for_path(relative_path)
    if not entries:
        return raw
    if any(
        entry.whole_file
        and (entry.backup_policy == "never_backup" or not include_credentials)
        for entry in entries
    ):
        return None

    fields = tuple(
        field
        for entry in entries
        if (
            entry.backup_policy == "never_backup"
            or (entry.backup_policy == "redact_default" and not include_credentials)
        )
        for field in entry.secret_fields
        if field != "$"
    )
    if not fields:
        return raw
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # A mixed file that cannot be safely field-redacted is omitted rather
        # than copied with unknown credential content.
        return None
    if not isinstance(payload, dict):
        return None
    for selector in fields:
        _remove_selector(payload, selector.split("."))
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def preserve_local_secret_bytes(
    relative_path: str | PurePosixPath,
    restored: bytes,
    local: bytes | None,
) -> bytes:
    """Merge omitted/redacted mixed-file secrets from the current machine."""

    if local is None:
        return restored
    entries = tuple(
        entry for entry in inventory_for_path(relative_path) if not entry.whole_file
    )
    if not entries:
        return restored
    try:
        incoming = json.loads(restored)
        current = json.loads(local)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return restored
    if not isinstance(incoming, dict) or not isinstance(current, dict):
        return restored
    for entry in entries:
        for selector in entry.secret_fields:
            _preserve_selector(
                incoming,
                current,
                selector.split("."),
                always_local=entry.backup_policy == "never_backup",
            )
    return (json.dumps(incoming, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _remove_selector(node: object, parts: list[str]) -> None:
    if not isinstance(node, dict) or not parts:
        return
    head, *tail = parts
    if head == "*":
        for child in node.values():
            _remove_selector(child, tail)
    elif not tail:
        node.pop(head, None)
    elif head in node:
        _remove_selector(node[head], tail)


def _preserve_selector(
    incoming: object,
    current: object,
    parts: list[str],
    *,
    always_local: bool,
) -> bool:
    if not isinstance(incoming, dict) or not isinstance(current, dict) or not parts:
        return False
    head, *tail = parts
    if head == "*":
        preserved = False
        for key in incoming.keys() & current.keys():
            preserved = (
                _preserve_selector(
                    incoming[key],
                    current[key],
                    tail,
                    always_local=always_local,
                )
                or preserved
            )
        return preserved
    if not tail:
        if head not in current:
            return False
        if always_local or head not in incoming or _is_redacted(incoming[head]):
            incoming[head] = current[head]
            return True
        elif isinstance(incoming[head], dict) and isinstance(current[head], dict):
            preserved = False
            for key, value in current[head].items():
                if key not in incoming[head] or _is_redacted(incoming[head][key]):
                    incoming[head][key] = value
                    preserved = True
            return preserved
        return False
    if head not in current or not isinstance(current[head], dict):
        return False
    if head in incoming:
        return _preserve_selector(
            incoming[head],
            current[head],
            tail,
            always_local=always_local,
        )
    restored_parent: dict[str, object] = {}
    if _preserve_selector(
        restored_parent,
        current[head],
        tail,
        always_local=always_local,
    ):
        incoming[head] = restored_parent
        return True
    return False


def is_redacted_value(value: object) -> bool:
    """Whether ``value`` is a display form rather than a real secret.

    The single authority for "this came back from a masked projection":
    backup restore uses it to keep a local secret, and the editing paths
    use it to refuse a mask submitted as if it were a new value.
    """

    if value is None:
        return True
    if isinstance(value, str):
        lowered = value.strip().casefold()
        return (
            lowered in {"redacted", "<redacted>", "[redacted]", "***redacted***"}
            or value == "•" * 8
            or "…" in value
        )
    return False


_is_redacted = is_redacted_value


CredentialStatus = Literal[
    "permission",
    "symlink",
    "not_regular",
    "foreign_owner",
    "stale_temporary",
]

# A temporary file younger than this may still belong to a live writer, so
# only older leftovers are reported and removed.
_STALE_TEMPORARY_AGE = 3600.0


@dataclass(frozen=True)
class CredentialFinding:
    """One inventory-driven defect: kind, path, and status only.

    A finding never carries a secret value, a file's contents, or an
    absolute path outside the state root, because it is printed by
    ``openprogram doctor credentials`` and serialised to JSON.
    """

    kind: str
    relative_path: str
    status: CredentialStatus
    repairable: bool
    repaired: bool = False
    _identity: tuple[int, int] | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "status": self.status,
            "repairable": self.repairable,
            "repaired": self.repaired,
        }


def _temporary_of(name: str) -> str | None:
    """Return the target name a private temporary file belongs to."""

    if not name.startswith(".") or not name.endswith(".tmp"):
        return None
    stem = name[1:-4]
    target, _, token = stem.rpartition(".")
    if (
        not target
        or len(token) != 24
        or any(c not in "0123456789abcdef" for c in token)
    ):
        return None
    return target


def _inspect(path: Path, relative: str, kind: str) -> CredentialFinding | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return CredentialFinding(kind, relative, "symlink", False)
    if os.name != "nt" and info.st_uid != os.geteuid():
        return CredentialFinding(kind, relative, "foreign_owner", False)
    if stat.S_ISDIR(info.st_mode):
        expected = 0o700
    elif stat.S_ISREG(info.st_mode):
        expected = 0o600
    else:
        return CredentialFinding(kind, relative, "not_regular", False)
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != expected:
        return CredentialFinding(kind, relative, "permission", True)
    return None


def audit_credentials(*, root: Path) -> list[CredentialFinding]:
    """Report every inventory-registered path that is not owner-only.

    The scan uses ``lstat`` throughout, so a symlink is reported as a
    symlink and never followed to its target.
    """

    root_path = Path(root)
    findings: list[CredentialFinding] = []
    seen: set[str] = set()
    for current_dir, dir_names, file_names in os.walk(root_path, followlinks=False):
        here = Path(current_dir)
        for name in sorted(dir_names) + sorted(file_names):
            path = here / name
            relative = path.relative_to(root_path).as_posix()
            if relative in seen:
                continue
            entries = inventory_for_path(relative)
            if not entries and path.is_symlink():
                entries = tuple(
                    entry
                    for entry in SECRET_INVENTORY
                    if _can_be_inventory_parent(relative, entry.path_pattern)
                )
            target = _temporary_of(name)
            if not entries and target is not None:
                # A leftover temporary only matters beside a registered target.
                entries = inventory_for_path(
                    (path.parent / target).relative_to(root_path).as_posix()
                )
                stale_info = _stale_temporary_info(path) if entries else None
                if entries and stale_info is not None:
                    seen.add(relative)
                    findings.append(
                        CredentialFinding(
                            entries[0].kind,
                            relative,
                            "stale_temporary",
                            True,
                            _identity=(stale_info.st_dev, stale_info.st_ino),
                        )
                    )
                continue
            if not entries:
                continue
            seen.add(relative)
            finding = _inspect(path, relative, entries[0].kind)
            if finding is not None:
                findings.append(finding)
            # Secret directories are the parents of registered files.
            for parent in path.parents:
                if parent == root_path:
                    break
                parent_relative = parent.relative_to(root_path).as_posix()
                if parent_relative in seen:
                    continue
                seen.add(parent_relative)
                parent_finding = _inspect(parent, parent_relative, entries[0].kind)
                if parent_finding is not None:
                    findings.append(parent_finding)
    return sorted(findings, key=lambda f: (f.relative_path, f.status))


def _can_be_inventory_parent(relative: str, pattern: str) -> bool:
    parts = PurePosixPath(relative).parts
    pattern_parts = PurePosixPath(pattern).parts
    return len(parts) < len(pattern_parts) and all(
        fnmatch.fnmatchcase(part, pattern_part)
        for part, pattern_part in zip(parts, pattern_parts)
    )


def _stale_temporary_info(path: Path) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if time.time() - info.st_mtime <= _STALE_TEMPORARY_AGE:
        return None
    return info


def _open_parent_below_root(root: Path, relative: Path) -> int:
    """Open ``relative`` below ``root`` without following directory links."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _repair_posix_mode(
    root: Path, relative: Path, expected: os.stat_result, mode: int
) -> bool:
    """Change mode through a no-follow descriptor for the audited inode."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if stat.S_ISDIR(expected.st_mode):
        flags |= getattr(os, "O_DIRECTORY", 0)
    parent = _open_parent_below_root(root, relative.parent)
    try:
        descriptor = os.open(relative.name, flags, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                return False
            if opened.st_uid != os.geteuid():
                return False
            if not (stat.S_ISREG(opened.st_mode) or stat.S_ISDIR(opened.st_mode)):
                return False
            os.fchmod(descriptor, mode)
            verified = os.fstat(descriptor)
            return (verified.st_dev, verified.st_ino) == (
                expected.st_dev,
                expected.st_ino,
            ) and stat.S_IMODE(verified.st_mode) == mode
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _remove_stale_temporary(
    root: Path, relative: Path, identity: tuple[int, int] | None
) -> bool:
    parent = _open_parent_below_root(root, relative.parent)
    try:
        info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if identity != (info.st_dev, info.st_ino):
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        if time.time() - info.st_mtime <= _STALE_TEMPORARY_AGE:
            return False
        os.unlink(relative.name, dir_fd=parent)
        os.fsync(parent)
        return True
    finally:
        os.close(parent)


def repair_credentials(*, root: Path) -> list[CredentialFinding]:
    """Repair only current-user regular files and secret directories.

    Ownership is never taken and symlinks are never followed, so a
    foreign-owner or symlink finding survives repair and keeps the
    command's exit status non-zero.
    """

    root_path = Path(root)
    repaired: list[CredentialFinding] = []
    for finding in audit_credentials(root=root_path):
        if not finding.repairable:
            repaired.append(finding)
            continue
        path = root_path / finding.relative_path
        done = False
        try:
            if finding.status == "stale_temporary":
                target_name = _temporary_of(path.name)
                if target_name is not None:
                    with _private_file_lock(
                        path.with_name(target_name), root=root_path
                    ):
                        done = _remove_stale_temporary(
                            root_path,
                            Path(finding.relative_path),
                            finding._identity,
                        )
            else:
                info = os.lstat(path)
                # Re-check under the same rules the audit used: never
                # widen the blast radius between scan and repair.
                if not stat.S_ISLNK(info.st_mode) and (
                    os.name == "nt" or info.st_uid == os.geteuid()
                ):
                    mode = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
                    if os.name == "nt":
                        _apply_windows_owner_acl(path)
                        done = True
                    else:
                        done = _repair_posix_mode(
                            root_path, Path(finding.relative_path), info, mode
                        )
        except OSError:
            done = False
        repaired.append(
            CredentialFinding(
                finding.kind,
                finding.relative_path,
                finding.status,
                finding.repairable,
                done,
                finding._identity,
            )
        )
    return repaired


class PrivateAtomicWriteError(OSError):
    """A private atomic write failed at a named durability boundary."""

    def __init__(
        self,
        code: str,
        path: Path,
        *,
        committed: bool,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(f"{code} failed for {path}")
        self.code = code
        self.path = path
        self.committed = committed
        self.__cause__ = cause


@dataclass(frozen=True)
class PrivateAtomicWriteResult:
    """Revision of the exact bytes published by one successful write."""

    revision: str


_MISSING_REVISION = (
    "missing:sha256:"
    + hashlib.sha256(b"openprogram-private-file-missing-v1").hexdigest()
)
_held_locks = threading.local()


def _revision(raw: bytes | None) -> str:
    if raw is None:
        return _MISSING_REVISION
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _ensure_private_root(root: Path) -> Path:
    """Create or validate the owner-only state root without following it."""

    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    try:
        info = os.lstat(root)
    except OSError as exc:
        raise PrivateAtomicWriteError(
            "directory", root, committed=False, cause=exc
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise PrivateAtomicWriteError("symlink", root, committed=False)
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateAtomicWriteError("directory", root, committed=False)
    _verify_owner(root, info)
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(root, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise PrivateAtomicWriteError("directory", root, committed=False)
            _verify_owner(root, opened)
            os.fchmod(descriptor, 0o700)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                raise PrivateAtomicWriteError("permission", root, committed=False)
        finally:
            os.close(descriptor)
    else:
        _apply_windows_owner_acl(root)
    return root


def _ensure_private_directory(path: Path, *, root: Path) -> Path:
    """Create one owner-only directory below ``root`` without following links."""

    root_path = Path(root).absolute()
    root_resolved = _ensure_private_root(root_path)
    requested = Path(path).absolute()
    if requested in {root_path, root_resolved}:
        return root_resolved
    relative = _relative_below_root(requested, root_path, root_resolved)
    current = root_resolved
    for part in relative.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise PrivateAtomicWriteError("symlink", current, committed=False)
        _verify_owned_directory(current, info)
    return current


def _private_atomic_write(
    path: Path,
    writer: Callable[[BinaryIO], object],
    *,
    root: Path,
    expected_revision: str | None = None,
    lock_timeout: float = 10.0,
) -> PrivateAtomicWriteResult:
    """Write one owner-only file under a stable sibling lock.

    ``expected_revision`` is optional for compatibility with existing full
    replacement callers.  When supplied, it must match the fingerprint of the
    exact current bytes (or the stable missing-file fingerprint).  A manual
    edit made while the callback runs is also detected before publication.
    """

    with _private_file_lock(path, root=root, timeout=lock_timeout):
        baseline = private_file_revision(path, root=root, lock_timeout=lock_timeout)
        if expected_revision is not None and expected_revision != baseline:
            raise PrivateAtomicWriteError("conflict", Path(path), committed=False)
        return _private_atomic_publish(
            path,
            writer,
            root=root,
            baseline_revision=baseline,
            lock_timeout=lock_timeout,
        )


def _private_atomic_update(
    path: Path,
    updater: Callable[[bytes | None], bytes],
    *,
    root: Path,
    expected_revision: str | None = None,
    lock_timeout: float = 10.0,
) -> PrivateAtomicWriteResult:
    """Read, update, and publish exact bytes under one cross-process lock."""

    with _private_file_lock(path, root=root, timeout=lock_timeout):
        current = _read_private_bytes(path, root=root)
        baseline = _revision(current)
        if expected_revision is not None and expected_revision != baseline:
            raise PrivateAtomicWriteError("conflict", Path(path), committed=False)
        try:
            updated = updater(current)
            if not isinstance(updated, bytes):
                raise TypeError("private file updater must return bytes")
        except PrivateAtomicWriteError:
            raise
        except Exception as exc:
            raise PrivateAtomicWriteError(
                "serialization", Path(path), committed=False, cause=exc
            ) from exc
        return _private_atomic_write(
            path,
            lambda handle: handle.write(updated),
            root=root,
            expected_revision=baseline,
            lock_timeout=lock_timeout,
        )


def private_file_revision(
    path: Path,
    *,
    root: Path,
    lock_timeout: float = 10.0,
) -> str:
    """Fingerprint the exact current bytes, including a stable missing state."""

    with _private_file_lock(path, root=root, timeout=lock_timeout):
        return _revision(_read_private_bytes(path, root=root))


def _private_unlink(path: Path, *, root: Path, lock_timeout: float = 10.0) -> bool:
    """Delete one verified private regular file under its sibling lock."""

    with _private_file_lock(path, root=root, timeout=lock_timeout):
        root_path = Path(root).absolute()
        root_resolved = _ensure_private_root(root_path)
        relative = _relative_below_root(Path(path).absolute(), root_path, root_resolved)
        parent = _ensure_private_directory(
            root_resolved.joinpath(*relative.parent.parts), root=root_resolved
        )
        target = parent / relative.name
        try:
            before = os.lstat(target)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(before.st_mode):
            raise PrivateAtomicWriteError("symlink", target, committed=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(target, flags)
        try:
            opened = os.fstat(descriptor)
            _verify_private_regular_info(target, opened)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise PrivateAtomicWriteError("delete", target, committed=False)
            try:
                os.unlink(target)
            except OSError as exc:
                raise PrivateAtomicWriteError(
                    "delete", target, committed=False, cause=exc
                ) from exc
        finally:
            os.close(descriptor)
        try:
            os.lstat(target)
        except FileNotFoundError:
            if os.name != "nt":
                try:
                    directory = os.open(
                        parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    )
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as exc:
                    raise PrivateAtomicWriteError(
                        "committed_not_durable",
                        target,
                        committed=True,
                        cause=exc,
                    ) from exc
            return True
        raise PrivateAtomicWriteError("delete", target, committed=False)


@contextmanager
def _private_file_lock(
    path: Path,
    *,
    root: Path,
    timeout: float = 10.0,
) -> Iterator[None]:
    """Acquire the owner-only stable sibling lock for ``path``."""

    root_path = Path(root).absolute()
    root_resolved = _ensure_private_root(root_path)
    relative = _relative_below_root(Path(path).absolute(), root_path, root_resolved)
    parent = _ensure_private_directory(
        root_resolved.joinpath(*relative.parent.parts), root=root_resolved
    )
    target = parent / relative.name
    _verify_existing_target(target)
    key = os.fspath(target)
    held = getattr(_held_locks, "paths", set())
    if key in held:
        yield
        return

    lock_path = target.with_suffix(target.suffix + ".lock")
    try:
        _verify_existing_target(lock_path)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(lock_path, flags, 0o600)
    except PrivateAtomicWriteError as exc:
        raise PrivateAtomicWriteError(
            "lock", lock_path, committed=False, cause=exc
        ) from exc
    except OSError as exc:
        raise PrivateAtomicWriteError(
            "lock", lock_path, committed=False, cause=exc
        ) from exc

    acquired = False
    try:
        info = os.fstat(descriptor)
        _verify_owner(lock_path, info)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        else:
            _apply_windows_owner_acl(lock_path)
        _verify_private_regular_info(lock_path, os.fstat(descriptor))
        path_info = os.lstat(lock_path)
        if stat.S_ISLNK(path_info.st_mode) or (
            path_info.st_dev,
            path_info.st_ino,
        ) != (info.st_dev, info.st_ino):
            raise PrivateAtomicWriteError("lock", lock_path, committed=False)

        from openprogram import _compat as file_lock

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                file_lock.flock(descriptor, file_lock.LOCK_EX | file_lock.LOCK_NB)
                acquired = True
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise PrivateAtomicWriteError(
                        "lock_timeout", lock_path, committed=False, cause=exc
                    ) from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

        locked_info = os.lstat(lock_path)
        if stat.S_ISLNK(locked_info.st_mode) or (
            locked_info.st_dev,
            locked_info.st_ino,
        ) != (info.st_dev, info.st_ino):
            raise PrivateAtomicWriteError("lock", lock_path, committed=False)

        held.add(key)
        _held_locks.paths = held
        try:
            yield
        finally:
            held.remove(key)
    finally:
        if acquired:
            from openprogram import _compat as file_lock

            file_lock.flock(descriptor, file_lock.LOCK_UN)
        os.close(descriptor)


def _read_private_bytes(path: Path, *, root: Path) -> bytes | None:
    root_path = Path(root).absolute()
    root_resolved = _ensure_private_root(root_path)
    relative = _relative_below_root(Path(path).absolute(), root_path, root_resolved)
    parent = _ensure_private_directory(
        root_resolved.joinpath(*relative.parent.parts), root=root_resolved
    )
    target = parent / relative.name
    try:
        before = os.lstat(target)
    except FileNotFoundError:
        return None
    _verify_existing_target(target)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        return None
    try:
        opened = os.fstat(descriptor)
        if inventory_for_path(relative.as_posix()):
            _verify_private_regular_info(target, opened)
        else:
            _verify_owner(target, opened)
        if not stat.S_ISREG(opened.st_mode) or (
            before.st_dev,
            before.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise PrivateAtomicWriteError("read", target, committed=False)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _private_atomic_publish(
    path: Path,
    writer: Callable[[BinaryIO], object],
    *,
    root: Path,
    baseline_revision: str,
    lock_timeout: float,
) -> PrivateAtomicWriteResult:
    """Publish bytes while the caller holds the target's sibling lock."""

    root_path = Path(root).absolute()
    root_resolved = _ensure_private_root(root_path)
    relative = _relative_below_root(Path(path).absolute(), root_path, root_resolved)
    parent = _ensure_private_directory(
        root_resolved.joinpath(*relative.parent.parts),
        root=root_resolved,
    )
    target = parent / relative.name
    _verify_existing_target(target)
    temporary, descriptor = _open_unique_private_temp(parent, target.name)
    committed = False
    try:
        try:
            with os.fdopen(descriptor, "w+b") as handle:
                writer(handle)
                handle.flush()
                if os.name != "nt":
                    os.fchmod(handle.fileno(), 0o600)
                else:
                    _apply_windows_owner_acl(temporary)
                info = os.fstat(handle.fileno())
                _verify_private_regular_info(temporary, info)
                try:
                    os.fsync(handle.fileno())
                except OSError as exc:
                    raise PrivateAtomicWriteError(
                        "fsync",
                        target,
                        committed=False,
                        cause=exc,
                    ) from exc
        except PrivateAtomicWriteError:
            raise
        except OSError as exc:
            raise PrivateAtomicWriteError(
                "write",
                target,
                committed=False,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise PrivateAtomicWriteError(
                "serialization",
                target,
                committed=False,
                cause=exc,
            ) from exc
        if (
            private_file_revision(target, root=root_resolved, lock_timeout=lock_timeout)
            != baseline_revision
        ):
            raise PrivateAtomicWriteError("conflict", target, committed=False)
        try:
            os.replace(temporary, target)
            committed = True
        except OSError as exc:
            raise PrivateAtomicWriteError(
                "replace",
                target,
                committed=False,
                cause=exc,
            ) from exc
        try:
            _restrict_and_verify_file(target)
        except OSError as exc:
            raise PrivateAtomicWriteError(
                "permission",
                target,
                committed=True,
                cause=exc,
            ) from exc
        if os.name != "nt":
            try:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_fd = os.open(parent, flags)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise PrivateAtomicWriteError(
                    "committed_not_durable",
                    target,
                    committed=True,
                    cause=exc,
                ) from exc
        return PrivateAtomicWriteResult(
            revision=private_file_revision(
                target, root=root_resolved, lock_timeout=lock_timeout
            )
        )
    finally:
        if not committed:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _relative_below_root(path: Path, root: Path, resolved_root: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError:
        try:
            relative = path.relative_to(resolved_root)
        except ValueError as exc:
            raise PrivateAtomicWriteError(
                "outside_root", path, committed=False
            ) from exc
    if not relative.parts or ".." in relative.parts:
        raise PrivateAtomicWriteError("outside_root", path, committed=False)
    return relative


def _verify_owned_directory(path: Path, info: os.stat_result | None = None) -> None:
    info = info or os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateAtomicWriteError("directory", path, committed=False)
    _verify_owner(path, info)
    if os.name != "nt":
        os.chmod(path, 0o700)
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o700:
            raise PrivateAtomicWriteError("permission", path, committed=False)
    else:
        _apply_windows_owner_acl(path)


def _verify_existing_target(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise PrivateAtomicWriteError("symlink", path, committed=False)
    if not stat.S_ISREG(info.st_mode):
        raise PrivateAtomicWriteError(
            "target is not a regular file", path, committed=False
        )
    _verify_owner(path, info)


def _verify_owner(path: Path, info: os.stat_result) -> None:
    if os.name != "nt" and info.st_uid != os.geteuid():
        raise PrivateAtomicWriteError("foreign_owner", path, committed=False)


def _open_unique_private_temp(parent: Path, target_name: str) -> tuple[Path, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _ in range(32):
        temporary = parent / f".{target_name}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError:
            continue
        if os.name == "nt":  # POSIX mode is effective at os.open time.
            try:
                _restrict_and_verify_file(temporary)
            except OSError:
                os.close(descriptor)
                temporary.unlink(missing_ok=True)
                raise
        return temporary, descriptor
    raise PrivateAtomicWriteError("temporary_collision", parent, committed=False)


def _verify_private_regular_info(path: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise PrivateAtomicWriteError(
            "temporary is not a regular file", path, committed=False
        )
    _verify_owner(path, info)
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
        raise PrivateAtomicWriteError("permission", path, committed=False)


def _restrict_and_verify_file(path: Path) -> None:
    if os.name == "nt":
        _apply_windows_owner_acl(path)
    else:
        from openprogram._compat import restrict_to_user

        restrict_to_user(path)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("published path is not a regular file")
    if os.name != "nt":
        if info.st_uid != os.geteuid():
            raise OSError("published file has a foreign owner")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise OSError("published file is not owner-only")


def _apply_windows_owner_acl(path: Path) -> None:
    """Apply and read back a non-inherited current-user + SYSTEM ACL."""

    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        raise OSError("cannot determine the current Windows user")
    applied = _run_icacls(
        [
            "icacls",
            os.fspath(path),
            "/inheritance:r",
            "/grant:r",
            f"{user}:(F)",
            "/grant:r",
            "SYSTEM:(F)",
        ],
    )
    if applied.returncode != 0:
        raise OSError("could not apply owner-only Windows ACL")
    verified = _run_icacls(["icacls", os.fspath(path)])
    if verified.returncode != 0:
        raise OSError("could not read back owner-only Windows ACL")

    principals: set[str] = set()
    path_text = os.fspath(path)
    for raw_line in verified.stdout.splitlines():
        line = raw_line.strip()
        if line.casefold().startswith(path_text.casefold()):
            line = line[len(path_text) :].strip()
        if ":(" not in line:
            continue
        principal, permissions = line.split(":", 1)
        principal = principal.strip().casefold()
        if "(I)" in permissions or "(F)" not in permissions:
            raise OSError("Windows ACL is inherited or not full-control")
        principals.add(principal)

    lowered_user = user.casefold()
    user_present = any(
        principal == lowered_user or principal.endswith("\\" + lowered_user)
        for principal in principals
    )
    system_present = any(
        principal == "system" or principal.endswith("\\system")
        for principal in principals
    )
    for principal in principals:
        if (
            principal != lowered_user
            and not principal.endswith("\\" + lowered_user)
            and principal != "system"
            and not principal.endswith("\\system")
        ):
            raise OSError("Windows ACL has an unexpected principal")
    if not user_present or not system_present:
        raise OSError("Windows ACL is missing current-user or SYSTEM access")


def _run_icacls(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("Windows ACL command failed") from exc


__all__ = [
    "BackupPolicy",
    "CredentialFinding",
    "CredentialStatus",
    "DeleteAction",
    "PrivateAtomicWriteResult",
    "PrivateAtomicWriteError",
    "SECRET_INVENTORY",
    "SecretInventoryEntry",
    "SecretLifecycle",
    "audit_credentials",
    "backup_bytes",
    "inventory_for_path",
    "is_redacted_value",
    "private_file_revision",
    "preserve_local_secret_bytes",
    "repair_credentials",
]
