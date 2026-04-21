"""Adopt credentials from the Qwen Code CLI.

Qwen Code (Alibaba's Claude-Code analogue) stores OAuth tokens at
``~/.qwen/oauth_creds.json``:

  {
    "access_token": "...",
    "refresh_token": "...",
    "token_type": "Bearer",
    "resource_url": "portal.qwen.ai",
    "expiry_date": 1712345678901    # unix ms
  }

The shape matches ``OAuthPayload`` more naturally than Claude/Codex do —
expiry is present — but we still adopt it as delegated (``read_only``)
so rotations performed by Qwen CLI propagate without our store drifting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..types import (
    CliDelegatedPayload,
    Credential,
    CredentialSource,
    RemovalStep,
)


@dataclass
class QwenCliSource:
    """Reads ``~/.qwen/oauth_creds.json`` and adopts it read-only."""

    provider_id: str = "qwen"
    profile_id: str = "default"
    override_path: str = ""

    source_id: str = "qwen_cli"

    def _resolve_path(self) -> Path:
        if self.override_path:
            return Path(self.override_path).expanduser()
        return Path.home() / ".qwen" / "oauth_creds.json"

    def try_import(self, profile_root: Path) -> list[Credential]:
        path = self._resolve_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not data.get("access_token"):
            return []

        metadata = {
            "imported_from": self.source_id,
            "source_path": str(path),
        }
        if data.get("resource_url"):
            metadata["resource_url"] = data["resource_url"]
        if data.get("token_type"):
            metadata["token_type"] = data["token_type"]

        return [
            Credential(
                provider_id=self.provider_id,
                profile_id=self.profile_id,
                kind="cli_delegated",
                payload=CliDelegatedPayload(
                    store_path=str(path),
                    access_key_path=["access_token"],
                    refresh_key_path=["refresh_token"],
                    expires_key_path=["expiry_date"],
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
                    f"Run `qwen logout` to remove {path}. OpenProgram does "
                    f"not delete the Qwen CLI's file — it belongs to the "
                    f"Qwen CLI. Skipping this step will cause the "
                    f"credential to be re-imported on the next restart."
                ),
                executable=False,
                kind="external_cli",
                target=path,
            )
        ]
