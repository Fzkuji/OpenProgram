"""Adopt credentials from the GitHub CLI (``gh``).

``gh auth login`` stores per-host entries at ``~/.config/gh/hosts.yml``:

  github.com:
    user: fzkuji
    oauth_token: gho_XXX…
    git_protocol: ssh
    users:
      fzkuji:
        oauth_token: gho_XXX…

The ``gh`` token is a plain bearer — we produce an ``api_key`` credential
rather than ``cli_delegated`` because the file's YAML parsing is
non-trivial and ``gh`` rotates the token infrequently. We snapshot-copy
into our store at import time; later rotations by ``gh`` won't
automatically propagate, but the user's ``gh auth status`` will warn
them and a re-import takes one click.

(If a provider needed ``gh`` rotation to propagate automatically, a
``CliDelegatedPayload`` with a YAML path walker would be the replacement.
The shape is forward-compat — we can upgrade without breaking consumers.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..types import (
    ApiKeyPayload,
    Credential,
    CredentialSource,
    RemovalStep,
)


@dataclass
class GhCliSource:
    """Reads ``~/.config/gh/hosts.yml`` and adopts one credential per host."""

    provider_id: str = "github"
    # ``gh`` supports multiple hosts (github.com + enterprise installs).
    # The filter lets callers restrict to one if they care; empty = all.
    hosts: list[str] = field(default_factory=list)
    override_path: str = ""

    source_id: str = "gh_cli"

    def _resolve_path(self) -> Path:
        if self.override_path:
            return Path(self.override_path).expanduser()
        return Path.home() / ".config" / "gh" / "hosts.yml"

    def try_import(self, profile_root: Path) -> list[Credential]:
        path = self._resolve_path()
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        parsed = _parse_hosts_yml(text)
        if not parsed:
            return []

        out: list[Credential] = []
        for host, fields in parsed.items():
            if self.hosts and host not in self.hosts:
                continue
            token = fields.get("oauth_token")
            if not token:
                continue
            metadata = {
                "imported_from": self.source_id,
                "source_path": str(path),
                "host": host,
            }
            if fields.get("user"):
                metadata["user"] = fields["user"]
            # Profile id encodes the host so two hosts don't collide.
            profile_id = "default" if host == "github.com" else host
            out.append(
                Credential(
                    provider_id=self.provider_id,
                    profile_id=profile_id,
                    kind="api_key",
                    payload=ApiKeyPayload(api_key=token),
                    source=self.source_id,
                    metadata=metadata,
                    # Snapshot copy — rotations by `gh` don't propagate,
                    # but we also don't block local rotation (user could
                    # paste a new key in our UI). So not read_only.
                    read_only=False,
                )
            )
        return out

    def removal_steps(self, cred: Credential) -> list[RemovalStep]:
        host = cred.metadata.get("host", "github.com")
        return [
            RemovalStep(
                description=(
                    f"Run `gh auth logout --hostname {host}` to remove "
                    f"the corresponding entry from ~/.config/gh/hosts.yml. "
                    f"If you skip this step, OpenProgram will see the "
                    f"token again on the next import sweep and offer to "
                    f"re-adopt it."
                ),
                executable=False,
                kind="external_cli",
                target=host,
            ),
            RemovalStep(
                description=f"Delete OpenProgram's copy of the {host} token.",
                executable=True,
                kind="file",
                target=cred.credential_id,
            ),
        ]


_HOST_LINE = re.compile(r"^([^\s#][^:]*):\s*$")
_FIELD_LINE = re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$")


def _parse_hosts_yml(text: str) -> dict[str, dict[str, str]]:
    """Very small YAML subset parser for ``gh``'s ``hosts.yml``.

    We avoid a PyYAML dependency because ``hosts.yml``'s actual shape is
    a trivial subset (top-level hostname keys with two-space-indented
    scalar fields). Accepts inline string values, ignores nested ``users:``
    maps — those duplicate top-level fields. Returns
    ``{host: {field: value}}``.

    This parser is not a general YAML parser. It's defensive enough to
    reject malformed input (missing colon, bad indentation) by simply
    skipping the offending line, which matches the "best effort" contract
    of ``try_import``.
    """
    result: dict[str, dict[str, str]] = {}
    current_host: str | None = None
    in_nested_block = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # New top-level key — a host.
        if not line.startswith(" ") and not line.startswith("\t"):
            m = _HOST_LINE.match(line)
            if not m:
                current_host = None
                continue
            current_host = m.group(1).strip()
            result[current_host] = {}
            in_nested_block = False
            continue
        if current_host is None:
            continue
        # Nested ``users:`` block — contents there duplicate parent fields
        # for the active user. Skip until we return to two-space indent
        # with a scalar key.
        stripped = line.strip()
        if stripped == "users:":
            in_nested_block = True
            continue
        if in_nested_block:
            # Exit when we hit a two-space-indented scalar at the host
            # level again (unlikely but harmless). Otherwise stay in.
            if line.startswith("  ") and not line.startswith("    "):
                # ambiguous; be safe and skip this line
                pass
            continue
        m = _FIELD_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        # Strip YAML-style quotes if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[current_host][key] = value
    return result
