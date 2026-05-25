"""Import credentials from another CLI tool's on-disk auth store.

Used to adopt existing ``codex login`` / ``claude login`` / ``gh auth
login`` / ``qwen login`` state without asking the user to log in twice.
The import is **one-shot**: at login time we read the external file and
create a new :class:`Credential` in our own store, typically marked
``read_only=True`` so later refresh attempts don't touch the external
file (the external CLI owns it).

Two behaviours the method supports, configurable per source:

  * ``copy`` — read once, store our own independent copy. Subsequent
    CLI rotations don't propagate to us. Useful when we want to pin
    a specific frozen state or when the external CLI is about to be
    uninstalled.
  * ``link`` — create a :class:`CliDelegatedPayload` pointing at the
    external file. Every API call re-reads the file. Any rotation the
    external CLI does is picked up automatically. Downside: expiry
    becomes the external tool's responsibility — if they mess up, we
    see stale tokens.

Defaults to ``link`` for read-only adoption (most users want CLI
rotations to "just work"), but the caller can override.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..types import (
    CliDelegatedPayload,
    Credential,
    LoginMethod,
    LoginUi,
    OAuthPayload,
)


Mode = Literal["link", "copy"]


@dataclass
class CliImportConfig:
    """Describes where to find another CLI's auth file and how to parse it.

    Path-expressions are lists of keys that ``_walk`` follows through the
    JSON tree. They're explicit lists rather than a "``a.b.c``" string
    because some keys have dots in them (refresh-token scopes sometimes
    do) and ambiguity in string paths leads to silent mis-imports.
    """

    source_id: str                                # "codex_cli", "qwen_cli", …
    store_path: str                               # absolute path to external file
    access_path: list[str] = field(default_factory=list)
    refresh_path: list[str] = field(default_factory=list)
    expires_path: list[str] = field(default_factory=list)
    # Epochs on disk are sometimes seconds, sometimes ms, sometimes
    # ISO-8601. We normalize to ms.
    expires_unit: Literal["ms", "s", "iso"] = "ms"
    # Some fields (account_id, email) we pull out for display.
    metadata_paths: dict[str, list[str]] = field(default_factory=dict)
    mode: Mode = "link"
    # Which client_id the external CLI used to mint these. Important:
    # refreshing against the wrong client_id fails. Callers supply this
    # so refresh (if it happens) uses the right one.
    client_id_hint: str = ""


class CliImportMethod(LoginMethod):
    """Reads an external CLI's auth file and produces a :class:`Credential`.

    ``run(ui)`` ignores ``ui`` almost entirely — this isn't really an
    interactive flow. We keep it as a :class:`LoginMethod` so the
    settings UI lists it alongside real login methods ("Import from
    Codex CLI").
    """

    method_id = "cli_import"

    def __init__(
        self,
        provider_id: str,
        config: CliImportConfig,
        *,
        profile_id: str = "default",
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config
        self._profile_id = profile_id

    async def run(self, ui: LoginUi) -> Credential:
        path = Path(self._cfg.store_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"external auth store {path} not found — "
                "run that CLI's login command first"
            )
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"external auth file is not JSON: {e}") from e

        if self._cfg.mode == "link":
            # "Link" mode stores only a pointer. We don't copy the
            # secret bits into our own file, so leaking ours doesn't
            # leak the external tool's tokens.
            metadata = self._extract_metadata(data)
            return Credential(
                provider_id=self.provider_id,
                profile_id=self._profile_id,
                kind="cli_delegated",
                payload=CliDelegatedPayload(
                    store_path=str(path),
                    access_key_path=list(self._cfg.access_path),
                    refresh_key_path=list(self._cfg.refresh_path),
                    expires_key_path=list(self._cfg.expires_path),
                ),
                source=f"{self.method_id}:{self._cfg.source_id}",
                metadata=metadata,
                read_only=True,
            )

        # "Copy" mode — dereference right now and build an OAuth credential.
        access = _walk(data, self._cfg.access_path)
        refresh = _walk(data, self._cfg.refresh_path) if self._cfg.refresh_path else ""
        expires_raw = (
            _walk(data, self._cfg.expires_path) if self._cfg.expires_path else 0
        )
        expires_at_ms = _normalize_expires(expires_raw, self._cfg.expires_unit)
        metadata = self._extract_metadata(data)

        return Credential(
            provider_id=self.provider_id,
            profile_id=self._profile_id,
            kind="oauth",
            payload=OAuthPayload(
                access_token=str(access or ""),
                refresh_token=str(refresh or ""),
                expires_at_ms=expires_at_ms,
                client_id=self._cfg.client_id_hint,
            ),
            source=f"{self.method_id}:{self._cfg.source_id}",
            metadata=metadata,
            read_only=False,
        )

    def _extract_metadata(self, data: dict) -> dict:
        out: dict = {"imported_from": self._cfg.source_id, "imported_at_ms": int(time.time() * 1000)}
        for k, path in self._cfg.metadata_paths.items():
            try:
                v = _walk(data, path)
                if v is not None:
                    out[k] = v
            except KeyError:
                continue
        return out


def _walk(data, path: list[str]):
    """Follow ``path`` into ``data`` and return the leaf. ``KeyError`` if
    any step is missing — callers decide whether that's fatal."""
    cur = data
    for step in path:
        if isinstance(cur, list):
            cur = cur[int(step)]
        else:
            cur = cur[step]
    return cur


def _normalize_expires(value, unit: Literal["ms", "s", "iso"]) -> int:
    if value is None or value == "":
        return 0
    if unit == "ms":
        return int(value)
    if unit == "s":
        return int(value) * 1000
    # ISO-8601 — parse lazily to avoid dragging dateutil in when not needed.
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except ValueError:
        return 0
