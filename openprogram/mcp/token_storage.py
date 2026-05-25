"""File-backed ``TokenStorage`` for the MCP SDK's ``OAuthClientProvider``.

The SDK calls into this object for two pieces of state:

  * Tokens (``OAuthToken``) — the access/refresh tokens themselves.
  * Client info (``OAuthClientInformationFull``) — only populated when
    the server supports dynamic client registration (RFC 7591). For
    pre-registered clients this stays ``None``.

Both live in ``<state>/mcp_tokens/<server_name>.json`` so user-visible
state (config file in the same state dir) and ephemeral runtime data
stay near each other. Permissions are tightened to ``0600`` since the
file holds a bearer token.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .config import get_tokens_dir


class FileTokenStorage(TokenStorage):
    """One file per MCP server, holding tokens + (optional) client info.

    Reads are cheap (small JSON), so we don't bother caching in memory —
    the SDK calls these rarely (on auth + on each refresh).
    """

    def __init__(self, server_name: str) -> None:
        self._path: Path = get_tokens_dir() / f"{_sanitize(server_name)}.json"

    # -- TokenStorage protocol ---------------------------------------
    async def get_tokens(self) -> Optional[OAuthToken]:
        data = self._read()
        tok = data.get("tokens") if data else None
        if not isinstance(tok, dict):
            return None
        try:
            return OAuthToken.model_validate(tok)
        except Exception:  # noqa: BLE001 — corrupt file → treat as no tokens
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read() or {}
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> Optional[OAuthClientInformationFull]:
        data = self._read()
        info = data.get("client_info") if data else None
        if not isinstance(info, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(info)
        except Exception:  # noqa: BLE001
            return None

    async def set_client_info(self,
                              client_info: OAuthClientInformationFull) -> None:
        data = self._read() or {}
        data["client_info"] = client_info.model_dump(
            mode="json", exclude_none=True,
        )
        self._write(data)

    # -- helpers used by webui management ----------------------------
    def path(self) -> Path:
        return self._path

    def has_tokens(self) -> bool:
        data = self._read()
        return bool(data and isinstance(data.get("tokens"), dict))

    def clear(self) -> bool:
        """Delete the file. Returns True iff something was removed."""
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return False

    # -- internals ----------------------------------------------------
    def _read(self) -> Optional[dict]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001 — keep going on corrupt JSON
            return None

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``open(O_CREAT, mode=0o600)`` creates the file with the
        # restrictive perms in one syscall. The previous "write_text
        # then chmod" sequence left a brief window where the file was
        # world-readable, which matters since the payload is a bearer
        # token. Existing-file mode bits are NOT changed by O_CREAT
        # alone, so unlink first to be sure the perms come from our
        # ``mode`` argument and not from a leftover 0644 inode.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        # ``os.O_NOFOLLOW`` blocks symlink attacks against the temp
        # path. mode=0o600 sets owner-only read/write at creation time.
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            # Best-effort cleanup on write failure so a half-written tmp
            # file doesn't linger with a token-shaped name.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._path)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_-." else "_" for c in name)
