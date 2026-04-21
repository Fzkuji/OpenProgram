"""Adopt credentials from the Claude Code CLI / keychain.

Claude Code stores OAuth tokens in a platform-dependent way:

  * macOS — the system Keychain under service ``Claude Code-credentials``
  * Linux / Windows — ``~/.claude/.credentials.json`` (JSON file with the
    same shape as the Keychain payload)

Both surfaces land in the same shape once read:

  {
    "claudeAiOauth": {
      "accessToken": "...",
      "refreshToken": "...",
      "expiresAt": 1712345678901,    # unix ms
      "scopes": ["user:inference", "user:profile"],
      "subscriptionType": "pro"
    }
  }

We prefer the file when it exists (covers Linux+Windows and the debug
case where users dump the Keychain entry to a file); we fall back to
invoking ``security find-generic-password`` on macOS. Both read paths
produce the same :class:`CliDelegatedPayload` shape pointing at a
temp-materialized file — the manager then reads it back every call.

For simplicity, this source only handles the *file* path today. Keychain
adoption is a follow-up: it needs an ``external_process`` payload
pointing at ``security`` so rotations from the real Claude Code CLI
propagate. The kind + path are stable; the flow is what changes.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ..types import (
    CliDelegatedPayload,
    Credential,
    CredentialSource,
    RemovalStep,
)


@dataclass
class ClaudeCodeSource:
    """Reads ``~/.claude/.credentials.json`` and adopts it read-only."""

    provider_id: str = "anthropic-claude-code"
    profile_id: str = "default"
    override_path: str = ""

    source_id: str = "claude_code"

    def _resolve_path(self) -> Path:
        if self.override_path:
            return Path(self.override_path).expanduser()
        return Path.home() / ".claude" / ".credentials.json"

    def try_import(self, profile_root: Path) -> list[Credential]:
        path = self._resolve_path()
        if not path.exists():
            # Skip silently — the macOS keychain path isn't implemented
            # here yet. Returning [] rather than raising lets a future
            # Keychain-backed source coexist in the same discovery loop.
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        oauth = data.get("claudeAiOauth") or {}
        if not oauth.get("accessToken"):
            return []

        metadata = {
            "imported_from": self.source_id,
            "source_path": str(path),
            "platform": sys.platform,
        }
        if oauth.get("subscriptionType"):
            metadata["subscription_type"] = oauth["subscriptionType"]
        if oauth.get("scopes"):
            metadata["scopes"] = oauth["scopes"]

        return [
            Credential(
                provider_id=self.provider_id,
                profile_id=self.profile_id,
                kind="cli_delegated",
                payload=CliDelegatedPayload(
                    store_path=str(path),
                    access_key_path=["claudeAiOauth", "accessToken"],
                    refresh_key_path=["claudeAiOauth", "refreshToken"],
                    expires_key_path=["claudeAiOauth", "expiresAt"],
                ),
                source=self.source_id,
                metadata=metadata,
                read_only=True,
            )
        ]

    def removal_steps(self, cred: Credential) -> list[RemovalStep]:
        path = (
            cred.payload.store_path
            if cred.kind == "cli_delegated"
            else str(self._resolve_path())
        )
        return [
            RemovalStep(
                description=(
                    f"Run `claude logout` (Claude Code CLI) to remove "
                    f"{path}. On macOS the CLI may also clear a Keychain "
                    f"entry under service `Claude Code-credentials`. "
                    f"OpenProgram does not touch either — the Claude Code "
                    f"CLI owns them."
                ),
                executable=False,
                kind="external_cli",
                target=path,
            )
        ]
