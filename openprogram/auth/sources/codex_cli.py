"""Adopt credentials from the Codex CLI's on-disk auth file.

The Codex CLI stores OAuth tokens at ``~/.codex/auth.json`` (or
``$CODEX_HOME/auth.json`` when set). We read the file non-destructively
and register a :class:`CliDelegatedPayload` credential that points at it.
"Delegated" mode means we never copy the secret bytes into our own
store — every API call re-reads the file — so Codex CLI's own rotations
propagate to us for free.

Key shape in Codex's file (observed as of 2026-04):

  {
    "OPENAI_API_KEY": null,                    # unused when tokens present
    "tokens": {
      "id_token": "...",
      "access_token": "...",
      "refresh_token": "...",
      "account_id": "...",
    },
    "last_refresh": "2026-04-21T15:13:02.1234Z"
  }

Codex's access token doesn't carry an ``expires_at`` — the CLI refreshes
opportunistically on 401. We encode that by leaving ``expires_key_path``
empty; the manager then treats this as "trust the CLI, it'll refresh
when needed".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..types import (
    CliDelegatedPayload,
    Credential,
    CredentialSource,
    RemovalStep,
)


@dataclass
class CodexCliSource:
    """Reads ``~/.codex/auth.json`` and adopts its tokens read-only."""

    provider_id: str = "chatgpt-subscription"
    profile_id: str = "default"
    # Allow tests to point at a fake file without monkey-patching $HOME.
    override_path: str = ""

    source_id: str = "codex_cli"

    def _resolve_path(self) -> Path:
        if self.override_path:
            return Path(self.override_path).expanduser()
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home).expanduser() / "auth.json"
        return Path.home() / ".codex" / "auth.json"

    def try_import(self, profile_root: Path) -> list[Credential]:
        path = self._resolve_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt or unreadable file isn't a fatal error for import —
            # just means we can't adopt it. Leave the user's Codex CLI
            # alone; they'll see it work the next time they re-login there.
            return []
        tokens = data.get("tokens") or {}
        if not tokens.get("access_token") and not tokens.get("refresh_token"):
            return []

        metadata = {
            "imported_from": self.source_id,
            "source_path": str(path),
        }
        if tokens.get("account_id"):
            metadata["account_id"] = tokens["account_id"]

        return [
            Credential(
                provider_id=self.provider_id,
                profile_id=self.profile_id,
                kind="cli_delegated",
                payload=CliDelegatedPayload(
                    store_path=str(path),
                    access_key_path=["tokens", "access_token"],
                    refresh_key_path=["tokens", "refresh_token"],
                    # No expires_at in Codex's file — intentionally empty.
                    expires_key_path=[],
                ),
                source=self.source_id,
                metadata=metadata,
                read_only=True,
            )
        ]

    def removal_steps(self, cred: Credential) -> list[RemovalStep]:
        # We never wrote Codex's file, so we never delete it. The user has
        # to run `codex logout` themselves — anything else risks destroying
        # credentials the Codex CLI still wants.
        path = (
            cred.payload.store_path
            if cred.kind == "cli_delegated"
            else str(self._resolve_path())
        )
        return [
            RemovalStep(
                description=(
                    f"Run `codex logout` to remove {path}. OpenProgram does "
                    f"not delete the Codex CLI's file on its own — it "
                    f"belongs to the Codex CLI. If you skip this step, "
                    f"the credential will be re-imported on the next "
                    f"restart."
                ),
                executable=False,
                kind="external_cli",
                target=path,
            )
        ]
