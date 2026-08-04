"""File-backed ``TokenStorage`` for the MCP SDK's ``OAuthClientProvider``,
plus the provider subclass that makes the stored state survive restarts.

The SDK calls into the storage object for two pieces of state:

  * Tokens (``OAuthToken``) — the access/refresh tokens themselves.
  * Client info (``OAuthClientInformationFull``) — only populated when
    the server supports dynamic client registration (RFC 7591). For
    pre-registered clients this stays ``None``.

On top of the protocol we persist what the SDK forgets across
processes but needs for a *silent* reconnect:

  * ``expires_at`` — absolute expiry timestamp. ``OAuthToken`` only
    carries the relative ``expires_in``, so a freshly-started process
    can't tell an expired access token from a live one.
  * ``discovery`` — authorization-server metadata (token endpoint,
    protected-resource metadata). The SDK only discovers these inside
    its 401 handler; a cold-start refresh without them POSTs to the
    wrong ``/token`` fallback URL and fails.

Everything lives in ``<state>/mcp_tokens/<server_name>.json`` so
user-visible state (config file in the same state dir) and runtime
data stay near each other. Permissions are tightened to ``0600``
since the file holds a bearer token.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

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
        # Record the *absolute* expiry. ``expires_in`` is relative to
        # issuance, so on its own it can't tell a later process whether
        # the access token is still live.
        if tokens.expires_in is not None:
            data["expires_at"] = time.time() + int(tokens.expires_in)
        else:
            data.pop("expires_at", None)
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

    # -- restart-survival extras (not part of the SDK protocol) ------
    def expires_at(self) -> Optional[float]:
        """Absolute expiry timestamp of the stored access token."""
        data = self._read()
        v = data.get("expires_at") if data else None
        return float(v) if isinstance(v, (int, float)) else None

    def stored_redirect_port(self) -> Optional[int]:
        """Loopback port of the redirect_uri this client was registered
        with. Reusing it across restarts keeps our authorization
        requests consistent with the dynamic client registration —
        strict servers reject a redirect_uri the client never
        registered.
        """
        data = self._read()
        info = data.get("client_info") if data else None
        uris = info.get("redirect_uris") if isinstance(info, dict) else None
        for u in uris or []:
            parsed = urllib.parse.urlparse(str(u))
            if parsed.hostname in ("127.0.0.1", "localhost") and parsed.port:
                return parsed.port
        return None

    def set_discovery(self, discovery: dict) -> None:
        """Persist OAuth discovery state (token endpoint et al.)."""
        data = self._read() or {}
        data["discovery"] = discovery
        self._write(data)

    def get_discovery(self) -> Optional[dict]:
        data = self._read()
        disc = data.get("discovery") if data else None
        return disc if isinstance(disc, dict) else None

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
        # O_NOFOLLOW is POSIX-only — it doesn't exist on Windows CPython,
        # so reference it via getattr(..., 0) or this whole write crashes
        # with AttributeError before the file is even opened (breaking
        # every OAuth MCP token write on Windows).
        _open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        _open_flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, _open_flags, 0o600)
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
        # 0o600 on POSIX comes from the O_CREAT mode above; on Windows it
        # is a near-no-op, so harden the bearer-token file's ACL too.
        from openprogram._compat import restrict_to_user
        restrict_to_user(self._path)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_-." else "_" for c in name)


class PersistentOAuthProvider(OAuthClientProvider):
    """``OAuthClientProvider`` whose auth state survives process restarts.

    The stock provider (mcp<=1.27) has three gaps that together force a
    fresh browser consent on every reconnect once the access token has
    aged out:

    1. ``_initialize`` loads tokens from storage but never sets
       ``context.token_expiry_time`` — a stale access token therefore
       passes ``is_token_valid()``, gets sent, earns a 401, and the 401
       branch runs the FULL authorization flow (browser redirect). The
       refresh token is never tried on that path.
    2. Even with expiry known, a cold-start refresh POSTs to the
       ``<server>/token`` fallback because authorization-server
       metadata is only discovered inside the 401 branch — wrong URL
       for any server whose auth server is a separate host.
    3. A refresh response that omits ``refresh_token`` (allowed by
       RFC 6749 §6 — the old one stays valid) wipes the stored refresh
       token, so the *next* expiry forces a browser flow.

    Requires the storage to be :class:`FileTokenStorage` (it always is
    — this provider is constructed in one place).
    """

    def __init__(self, *args, storage: FileTokenStorage, **kwargs) -> None:
        super().__init__(*args, storage=storage, **kwargs)
        self._file_storage = storage

    async def _initialize(self) -> None:
        await super()._initialize()
        # Gap 1: restore the absolute expiry so an expired token routes
        # into the SDK's pre-request refresh path instead of 401→browser.
        expires_at = self._file_storage.expires_at()
        if expires_at is not None:
            self.context.token_expiry_time = expires_at
        # Gap 2: restore discovery so that refresh POSTs to the real
        # token endpoint. Best-effort — a corrupt blob just degrades to
        # the SDK's fallback behaviour.
        disc = self._file_storage.get_discovery() or {}
        if self.context.oauth_metadata is None and disc.get("oauth_metadata"):
            try:
                self.context.oauth_metadata = OAuthMetadata.model_validate(
                    disc["oauth_metadata"])
            except Exception:  # noqa: BLE001
                pass
        if (self.context.protected_resource_metadata is None
                and disc.get("protected_resource_metadata")):
            try:
                self.context.protected_resource_metadata = (
                    ProtectedResourceMetadata.model_validate(
                        disc["protected_resource_metadata"]))
            except Exception:  # noqa: BLE001
                pass
        if self.context.auth_server_url is None:
            url = disc.get("auth_server_url")
            if isinstance(url, str) and url:
                self.context.auth_server_url = url

    async def _handle_token_response(self, response: httpx.Response) -> None:
        await super()._handle_token_response(response)
        self._persist_discovery()

    async def _handle_refresh_response(self, response: httpx.Response) -> bool:
        # Gap 3: hold on to the previous refresh token when the server
        # doesn't rotate it in the response.
        prior = self.context.current_tokens
        ok = await super()._handle_refresh_response(response)
        current = self.context.current_tokens
        if (ok and prior is not None and prior.refresh_token
                and current is not None and not current.refresh_token):
            current.refresh_token = prior.refresh_token
            await self.context.storage.set_tokens(current)
        return ok

    def _persist_discovery(self) -> None:
        ctx = self.context
        self._file_storage.set_discovery({
            "oauth_metadata": (
                ctx.oauth_metadata.model_dump(mode="json", exclude_none=True)
                if ctx.oauth_metadata else None
            ),
            "protected_resource_metadata": (
                ctx.protected_resource_metadata.model_dump(
                    mode="json", exclude_none=True)
                if ctx.protected_resource_metadata else None
            ),
            "auth_server_url": ctx.auth_server_url,
        })
