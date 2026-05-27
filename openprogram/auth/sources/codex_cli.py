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
    OAuthPayload,
    RemovalStep,
)


def _codex_binary_present() -> bool:
    """True if a ``codex`` (or ``codex.exe`` on Windows) executable is
    on PATH and can be invoked. We treat presence as "Codex CLI is
    actively maintaining ~/.codex/auth.json", which is the contract
    behind ``cli_delegated`` mode — every read re-checks the file so
    rotations from the external CLI propagate for free.

    Without the binary that contract is a lie: the file just rots,
    refresh tokens get consumed by other processes, and the in-process
    runtime ends up with stale bytes it can't refresh. In that case we
    should claim ownership (adopt as ``oauth``) so OpenProgram's own
    AuthManager can refresh the tokens.
    """
    import shutil
    return shutil.which("codex") is not None


@dataclass
class CodexCliSource:
    """Reads ``~/.codex/auth.json`` and adopts its tokens read-only."""

    provider_id: str = "openai-codex"
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

        # Two adoption modes, picked based on whether the Codex CLI is
        # actually around to maintain ~/.codex/auth.json:
        #
        #   * codex binary present → ``cli_delegated``. We just point at
        #     the file. Codex CLI handles refresh; we re-read on every
        #     use so rotations propagate. Read-only by design — only
        #     ``codex logout`` should mutate the file.
        #
        #   * codex binary missing → ``oauth``. The file is a one-shot
        #     snapshot of an OAuth session; nobody else will refresh it
        #     when the access_token expires. Copy the tokens into our
        #     own store and let AuthManager own refresh. Writable so
        #     refresh-token rotation actually persists.
        if _codex_binary_present():
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

        # Codex CLI not installed — bytes-copy the tokens. The Codex
        # OAuth token endpoint, client_id, and rotation scheme match
        # what ``providers/openai_codex/auth_adapter.register_codex_auth``
        # would refresh against, so AuthManager can take over cleanly.
        access_token = tokens.get("access_token") or ""
        refresh_token = tokens.get("refresh_token") or ""
        if not access_token or not refresh_token:
            # Nothing usable to adopt without the CLI to mediate.
            return []
        return [
            Credential(
                provider_id=self.provider_id,
                profile_id=self.profile_id,
                kind="oauth",
                payload=OAuthPayload(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at_ms=0,  # Codex CLI's file doesn't carry expiry
                    scope=["openid", "profile", "email", "offline_access"],
                    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
                    token_endpoint="https://auth.openai.com/oauth/token",
                    id_token=tokens.get("id_token") or "",
                    extra={"account_id": tokens.get("account_id") or ""},
                ),
                source=self.source_id,
                metadata=metadata,
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
