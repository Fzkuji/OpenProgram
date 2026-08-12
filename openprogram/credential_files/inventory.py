from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

SecretLifecycle = Literal["persistent", "ephemeral"]
BackupPolicy = Literal["redact_default", "include_on_opt_in", "never_backup"]
DeleteAction = Literal["clear_field", "unlink"]


@dataclass(frozen=True)
class SecretInventoryEntry:
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
        normalized = PurePosixPath(relative_path).as_posix().removeprefix("./")
        return fnmatch.fnmatchcase(normalized, self.path_pattern)


SECRET_INVENTORY = (
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
    return tuple(entry for entry in SECRET_INVENTORY if entry.matches(relative_path))


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


def backup_bytes(
    relative_path: str | PurePosixPath, raw: bytes, *, include_credentials: bool
) -> bytes | None:
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
        if entry.backup_policy == "never_backup"
        or (entry.backup_policy == "redact_default" and not include_credentials)
        for field in entry.secret_fields
        if field != "$"
    )
    if not fields:
        return raw
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for selector in fields:
        _remove_selector(payload, selector.split("."))
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def _preserve_selector(
    incoming: object, current: object, parts: list[str], *, always_local: bool
) -> bool:
    from .redaction import is_redacted_value

    if not isinstance(incoming, dict) or not isinstance(current, dict) or not parts:
        return False
    head, *tail = parts
    if head == "*":
        return any(
            _preserve_selector(
                incoming[key], current[key], tail, always_local=always_local
            )
            for key in incoming.keys() & current.keys()
        )
    if not tail:
        if head not in current:
            return False
        if always_local or head not in incoming or is_redacted_value(incoming[head]):
            incoming[head] = current[head]
            return True
        if isinstance(incoming[head], dict) and isinstance(current[head], dict):
            changed = False
            for key, value in current[head].items():
                if key not in incoming[head] or is_redacted_value(incoming[head][key]):
                    incoming[head][key] = value
                    changed = True
            return changed
        return False
    if head not in current or not isinstance(current[head], dict):
        return False
    if head in incoming:
        return _preserve_selector(
            incoming[head], current[head], tail, always_local=always_local
        )
    restored_parent: dict[str, object] = {}
    if _preserve_selector(
        restored_parent, current[head], tail, always_local=always_local
    ):
        incoming[head] = restored_parent
        return True
    return False


def preserve_local_secret_bytes(
    relative_path: str | PurePosixPath, restored: bytes, local: bytes | None
) -> bytes:
    if local is None:
        return restored
    entries = tuple(
        entry for entry in inventory_for_path(relative_path) if not entry.whole_file
    )
    if not entries:
        return restored
    try:
        incoming, current = json.loads(restored), json.loads(local)
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
    return (json.dumps(incoming, indent=2, sort_keys=True) + "\n").encode()
