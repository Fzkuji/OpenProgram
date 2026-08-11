"""Inventory and low-level file mechanics for OpenProgram-owned secrets.

The inventory is data, not a second storage layer.  It describes the existing
plaintext files so backup, restore, and later writer/doctor work use the same
classification.  This module does not read environment secrets or invoke
external credential helpers.
"""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Literal

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
        return fnmatch.fnmatchcase(normalized, self.path_pattern)


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
        "auth/*.json",
        ("$",),
        "openprogram.auth.store.AuthStore",
        "persistent",
        "include_on_opt_in",
        "unlink",
    ),
    SecretInventoryEntry(
        "profile_auth_store",
        "profiles/*/auth/*.json",
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


def _is_redacted(value: object) -> bool:
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


def _ensure_private_directory(path: Path, *, root: Path) -> Path:
    """Create one owner-only directory below ``root`` without following links."""

    root_path = Path(root).absolute()
    root_resolved = root_path.resolve(strict=True)
    _verify_owned_directory(root_resolved)
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
) -> None:
    """Write one owner-only file with same-directory atomic publication."""

    root_path = Path(root).absolute()
    root_resolved = root_path.resolve(strict=True)
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
    "DeleteAction",
    "PrivateAtomicWriteError",
    "SECRET_INVENTORY",
    "SecretInventoryEntry",
    "SecretLifecycle",
    "backup_bytes",
    "inventory_for_path",
    "preserve_local_secret_bytes",
]
