"""Generic PKCE + localhost-callback OAuth login.

Most ChatGPT / Anthropic / Google OAuth flows fit the same mold:

  1. Generate PKCE verifier + challenge
  2. Open the authorize URL in the user's browser with ``code_challenge``
  3. Start a local HTTP server on a known port
  4. The browser redirects back with ``?code=...``
  5. Exchange ``code`` + ``verifier`` → access_token + refresh_token

We implement the flow generically and accept a :class:`PkceConfig` that
captures the per-provider differences (client_id, authorize URL, token
URL, redirect URI, scopes, optional audience). Provider plugins
instantiate ``PkceLoginMethod(cfg)`` at registration time; the methods
registry ends up holding one configured method per provider that
supports PKCE.

Two optional fallbacks match the Hermes / openclaw UX:

  * Manual code paste — if the localhost callback can't bind (firewall,
    remote SSH session), the user pastes the redirect URL; we parse
    ``code`` out of it.
  * VPS hint — :class:`LoginUi` implementations can decide to show
    "open this URL on your local machine" instead of auto-opening.

The HTTP exchange uses ``httpx`` but doesn't import it at module load —
tests of the state machine don't need the network.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

from ..types import (
    Credential,
    LoginMethod,
    LoginUi,
    OAuthPayload,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PkceConfig:
    """Everything that varies per-provider for a PKCE flow.

    ``callback_port`` determines the ``REDIRECT_URI``. Providers are
    picky about that URI being pre-registered on the OAuth app server
    side, so we surface both the port and the path in config rather
    than hard-coding.

    ``extra_authorize_params`` holds provider-specific things like
    ``"audience"`` (Auth0-style) or ``"originator"`` (ChatGPT) that
    aren't part of the OAuth 2.0 core. Adding a new provider never
    means modifying this module — just a new config record.
    """

    authorize_url: str
    token_url: str
    client_id: str
    scopes: list[str] = field(default_factory=list)
    callback_port: int = 1455              # openclaw / pi-ai default for Codex
    callback_path: str = "/auth/callback"
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    extra_token_params: dict[str, str] = field(default_factory=dict)
    state_length_bytes: int = 16
    # Callback server binds to loopback only — don't ever advertise on
    # 0.0.0.0, that would let a process on the network intercept codes.
    # Bind IPv4 loopback explicitly. The OAuth app registers the
    # redirect_uri as http://localhost:1455/... — browsers typically
    # resolve `localhost` to 127.0.0.1 first, so we must listen there.
    # If we used `localhost` directly, aiohttp might resolve to IPv6 ::1
    # and the browser's IPv4 request would hit whoever else is on
    # 127.0.0.1:1455 (a stale codex-cli / pi-ai server, for example),
    # producing a confusing "state mismatch" error from that process.
    callback_host: str = "127.0.0.1"
    # Maximum time we wait for the browser to come back with a code.
    # Longer than you'd think — the user might get distracted, log in,
    # complete MFA, etc.
    timeout_seconds: float = 300.0


@dataclass
class PkceTokens:
    """Raw token-endpoint response; caller converts to :class:`Credential`."""

    access_token: str
    refresh_token: str
    expires_in: int
    id_token: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# LoginMethod implementation
# ---------------------------------------------------------------------------

def _generate_pkce(length_bytes: int = 32) -> tuple[str, str]:
    """Generate (verifier, challenge) for PKCE S256.

    RFC 7636 permits 43-128 chars; 32 random bytes → 43 chars after
    base64url encoding (minus padding), which is the minimum. Minimum
    is fine — the challenge hashes to 32 bytes of SHA-256 either way.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(length_bytes)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class PkceLoginMethod(LoginMethod):
    """OAuth 2.0 authorization code + PKCE.

    Instances are typically built once per provider at plugin registration
    time. The method is re-usable across many logins — nothing per-user
    is stored on the instance.
    """

    method_id = "pkce_oauth"

    def __init__(
        self,
        provider_id: str,
        config: PkceConfig,
        *,
        profile_id: str = "default",
        metadata: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config
        self._profile_id = profile_id
        self._metadata = dict(metadata or {})

    async def run(self, ui: LoginUi) -> Credential:
        verifier, challenge = _generate_pkce()
        state = secrets.token_hex(self._cfg.state_length_bytes)
        redirect_uri = f"http://{self._cfg.callback_host}:{self._cfg.callback_port}{self._cfg.callback_path}"

        params = {
            "client_id": self._cfg.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            **self._cfg.extra_authorize_params,
        }
        if self._cfg.scopes:
            params["scope"] = " ".join(self._cfg.scopes)
        auth_url = f"{self._cfg.authorize_url}?{urlencode(params)}"

        await ui.show_progress("Opening browser for authentication…")
        await ui.open_url(auth_url)

        # Race the callback server against a manual-paste prompt. Whichever
        # returns first wins. This is the openclaw pattern: give the user
        # an escape hatch if their browser + localhost don't cooperate.
        code = await _race_callback_and_manual_paste(
            ui=ui, cfg=self._cfg, expected_state=state,
        )

        tokens = await _exchange_code_for_tokens(
            cfg=self._cfg, code=code, verifier=verifier, redirect_uri=redirect_uri,
        )

        expires_at_ms = int(time.time() * 1000) + tokens.expires_in * 1000
        return Credential(
            provider_id=self.provider_id,
            profile_id=self._profile_id,
            kind="oauth",
            payload=OAuthPayload(
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at_ms=expires_at_ms,
                scope=list(self._cfg.scopes),
                client_id=self._cfg.client_id,
                token_endpoint=self._cfg.token_url,
                id_token=tokens.id_token,
                extra=tokens.extra,
            ),
            source=f"{self.method_id}:{self.provider_id}",
            metadata=self._metadata,
        )


# ---------------------------------------------------------------------------
# Callback + manual-paste race
# ---------------------------------------------------------------------------

async def _race_callback_and_manual_paste(
    *,
    ui: LoginUi,
    cfg: PkceConfig,
    expected_state: str,
) -> str:
    """Start a local HTTP server for the OAuth redirect and a manual-paste
    prompt at the same time. Return whichever resolves first.

    The prompt is offered eagerly (don't wait 15 s then offer it) because
    on remote SSH sessions the browser is never going to hit our
    localhost — waiting would make the user think the flow was broken.
    UI implementations that know they're local (terminal with a GUI
    available) can implement :meth:`LoginUi.prompt` as a never-settling
    future so they never prompt — that deferral happens in the UI, not
    here.
    """
    callback_task = asyncio.create_task(_run_callback_server(cfg, expected_state))
    prompt_task = asyncio.create_task(_ask_manual_paste(ui, expected_state))

    try:
        done, pending = await asyncio.wait(
            [callback_task, prompt_task],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=cfg.timeout_seconds,
        )
    finally:
        for t in (callback_task, prompt_task):
            if not t.done():
                t.cancel()

    if not done:
        raise TimeoutError(f"OAuth flow timed out after {cfg.timeout_seconds}s")
    # Propagate whichever completed first; cancellation is best-effort.
    return done.pop().result()


async def _run_callback_server(cfg: PkceConfig, expected_state: str) -> str:
    """Bind to localhost:<port>, accept one request, return the ``code``
    parameter after validating state. Raises if the request doesn't
    match or the port can't be bound."""
    try:
        from aiohttp import web  # type: ignore[import]
    except ImportError as e:
        raise RuntimeError(
            "aiohttp is required for the OAuth callback server. "
            "Install with: pip install aiohttp"
        ) from e

    code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def handle(request):
        qs = request.query
        got_state = qs.get("state", "")
        code = qs.get("code", "")
        if got_state != expected_state:
            if not code_future.done():
                code_future.set_exception(
                    RuntimeError("OAuth state mismatch — possible CSRF")
                )
            return web.Response(
                status=400,
                text="State mismatch. You can close this window.",
                content_type="text/plain",
            )
        if not code:
            err = qs.get("error") or "missing_code"
            if not code_future.done():
                code_future.set_exception(
                    RuntimeError(f"OAuth flow failed: {err}")
                )
            return web.Response(
                status=400,
                text=f"OAuth error: {err}. You can close this window.",
                content_type="text/plain",
            )
        if not code_future.done():
            code_future.set_result(code)
        return web.Response(
            text="Authorization complete. You can close this window.",
            content_type="text/plain",
        )

    app = web.Application()
    app.router.add_get(cfg.callback_path, handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.callback_host, cfg.callback_port)
    try:
        await site.start()
    except OSError as e:
        # EADDRINUSE — another process already owns 127.0.0.1:<port>
        # (stale codex-cli / pi-ai callback server, usually). Surface
        # this clearly so the user can kill the other process; don't
        # let the manual-paste arm be the only option without saying
        # why the callback won't fire.
        await runner.cleanup()
        raise RuntimeError(
            f"Can't bind {cfg.callback_host}:{cfg.callback_port} for the OAuth "
            f"callback ({e.__class__.__name__}: {e}). Another process is "
            f"already listening there — likely a stale `codex login` or "
            f"`pi` process. Find it with "
            f"`lsof -iTCP:{cfg.callback_port} -sTCP:LISTEN` and kill it, "
            f"then retry."
        ) from e
    try:
        return await code_future
    finally:
        await runner.cleanup()


async def _ask_manual_paste(ui: LoginUi, expected_state: str) -> str:
    """Prompt user to paste the redirect URL. Extract ``code`` out of it.

    Accepts three shapes:
      * a full redirect URL (``http://localhost:1455/auth/callback?code=…``)
      * just the query string (``code=…&state=…``)
      * just the code value

    We don't enforce state matching here because the callback server
    already handles that; if it times out and we fall through to manual
    paste, we trust the user has the right URL in their clipboard.
    """
    raw = await ui.prompt(
        "If the browser callback doesn't complete, paste the redirect URL here",
    )
    raw = raw.strip()
    if not raw:
        raise ValueError("no redirect URL supplied")
    if raw.startswith("http"):
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        code_values = qs.get("code") or []
        if not code_values:
            raise ValueError(f"no code in redirect URL: {raw}")
        return code_values[0]
    if "code=" in raw:
        qs = parse_qs(raw)
        code_values = qs.get("code") or []
        if code_values:
            return code_values[0]
    return raw


async def _exchange_code_for_tokens(
    *, cfg: PkceConfig, code: str, verifier: str, redirect_uri: str,
) -> PkceTokens:
    """POST the auth code + PKCE verifier to the token endpoint.

    Most providers accept form-urlencoded; a few (Anthropic) accept JSON.
    We try form first — it's the RFC default — and fall back to JSON if
    we see a 400 that complains about content type.
    """
    import httpx
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        **cfg.extra_token_params,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            cfg.token_url,
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 400 and "content-type" in resp.text.lower():
            resp = await client.post(cfg.token_url, json=params)
        if resp.status_code != 200:
            raise RuntimeError(
                f"token exchange failed: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()

    for key in ("access_token", "refresh_token", "expires_in"):
        if key not in data:
            raise RuntimeError(f"token response missing {key!r}: {data}")
    return PkceTokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_in=int(data["expires_in"]),
        id_token=data.get("id_token", ""),
        extra={k: v for k, v in data.items()
               if k not in ("access_token", "refresh_token", "expires_in", "id_token")},
    )
