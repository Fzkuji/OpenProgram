"""REST + SSE routes for auth v2.

Responsibilities:

  * surface the list of profiles and pools so the Settings UI can render
  * accept add/remove of credentials with uniform payload shapes (the UI
    doesn't need to know CredentialKind internals — it submits a
    ``type`` discriminator and we build the right dataclass)
  * expose a *discover* endpoint that runs every registered source
    without writing to the store, so the UI can show "we found Claude
    Code / Codex / env var tokens — adopt them?" and let the user opt in
    per credential
  * stream :class:`AuthEvent` broadcasts via Server-Sent Events so the UI
    can render "token refreshed", "re-login required" toasts in
    real-time

All secret material is masked on read (``sk-abc…123`` style) — full
secrets never leave the server unencrypted. The UI has no need for the
raw value; API calls read through :mod:`auth.resolver`.

A single ``APIRouter`` is exposed; :func:`create_app` in :mod:`server`
does ``app.include_router(auth_router)``. Keeping the routes in their
own module prevents ``server.py`` from growing past 3k lines and keeps
the auth concerns testable against a minimal FastAPI instance.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from openprogram.auth.manager import get_manager
from openprogram.auth.profiles import (
    DEFAULT_PROFILE_NAME,
    get_profile_manager,
)
from openprogram.auth.store import get_store
from openprogram.auth.types import (
    AuthConfigError,
    AuthEvent,
    AuthEventListener,
    Credential,
    CredentialData,
    CredentialPool,
)


router = APIRouter(prefix="/api/providers", tags=["providers"])


# ---------------------------------------------------------------------------
# Serialization — mask secrets before shipping to UI
# ---------------------------------------------------------------------------

def _mask(secret: str, *, keep_prefix: int = 6, keep_suffix: int = 4) -> str:
    """Return a shortened preview: ``sk-abc…z123``. Keeps enough of the
    prefix that the user can identify which key this is, enough of the
    suffix to copy-check, never enough to reconstruct."""
    if not secret:
        return ""
    if len(secret) <= keep_prefix + keep_suffix + 1:
        return "*" * len(secret)
    return f"{secret[:keep_prefix]}…{secret[-keep_suffix:]}"


def _payload_preview(cred: Credential) -> dict[str, Any]:
    """Return a dict-shaped, UI-safe view of a credential payload.

    We don't reuse :meth:`Credential.to_dict` because that's the storage
    format and includes the raw secret. This function masks every token-
    like field and adds a ``masked`` marker so UI code can show a copy
    button only if the underlying payload isn't delegated / external."""
    payload = cred.payload
    if payload.kind == "api_key":
        return {"type": "api_key", "api_key_preview": _mask(payload.auth_value)}
    if payload.kind == "oauth":
        return {
            "type": "oauth",
            "access_token_preview": _mask(payload.auth_value),
            "has_refresh_token": bool(payload.data.get("refresh_token")),
            "expires_at_ms": payload.data.get("expires_at_ms", 0),
            "client_id": payload.data.get("client_id"),
            "scope": payload.data.get("scope"),
        }
    if payload.kind == "device_code":
        return {
            "type": "device_code",
            "access_token_preview": _mask(payload.auth_value),
            "has_refresh_token": bool(payload.data.get("refresh_token")),
            "expires_at_ms": payload.data.get("expires_at_ms", 0),
        }
    if payload.kind == "cli_delegated":
        return {
            "type": "cli_delegated",
            "store_path": payload.data.get("store_path"),
            "access_key_path": payload.data.get("access_key_path"),
        }
    if payload.kind == "external_process":
        return {
            "type": "external_process",
            "command": payload.data.get("command"),
            "cache_seconds": payload.data.get("cache_seconds"),
        }
    if payload.kind == "sso":
        return {"type": "sso", "broker": payload.data.get("broker")}
    return {"type": cred.kind, "unknown": True}


def _credential_view(cred: Credential) -> dict[str, Any]:
    return {
        "credential_id": cred.credential_id,
        "kind": cred.kind,
        "provider_id": cred.provider_id,
        "profile_id": cred.profile_id,
        "status": cred.status,
        "source": cred.source,
        "metadata": cred.metadata,
        "created_at_ms": cred.created_at_ms,
        "updated_at_ms": cred.updated_at_ms,
        "cooldown_until_ms": cred.cooldown_until_ms,
        "last_used_at_ms": cred.last_used_at_ms,
        "use_count": cred.use_count,
        "last_error": cred.last_error,
        "read_only": cred.read_only,
        "payload": _payload_preview(cred),
    }


def _pool_view(pool: CredentialPool) -> dict[str, Any]:
    return {
        "provider_id": pool.provider_id,
        "profile_id": pool.profile_id,
        "strategy": pool.strategy,
        "fallback_chain": [list(t) for t in pool.fallback_chain],
        "credentials": [_credential_view(c) for c in pool.credentials],
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateProfileBody(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""


class AddCredentialBody(BaseModel):
    """Uniform credential-creation shape.

    ``type`` drives which payload we build. Validation of required
    fields happens here so endpoints return a 400 with a clear message
    rather than a 500 from the dataclass constructor."""

    type: str = Field(..., description="api_key | oauth | external_process")
    api_key: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at_ms: Optional[int] = None
    client_id: Optional[str] = None
    command: Optional[list[str]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

@router.get("/profiles")
def list_profiles() -> dict[str, Any]:
    pm = get_profile_manager()
    return {
        "profiles": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "description": p.description,
                "created_at_ms": p.created_at_ms,
                "root": str(p.root),
            }
            for p in pm.list_profiles()
        ],
        "default": DEFAULT_PROFILE_NAME,
    }


@router.post("/profiles")
def create_profile(body: CreateProfileBody) -> dict[str, Any]:
    pm = get_profile_manager()
    try:
        profile = pm.create_profile(
            body.name,
            display_name=body.display_name,
            description=body.description,
        )
    except AuthConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "name": profile.name,
        "display_name": profile.display_name,
        "description": profile.description,
        "root": str(profile.root),
    }


@router.delete("/profiles/{name}")
def delete_profile(name: str) -> dict[str, Any]:
    pm = get_profile_manager()
    try:
        pm.delete_profile(name)
    except AuthConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deleted": name}


# ---------------------------------------------------------------------------
# Pools + credentials
# ---------------------------------------------------------------------------

@router.get("/pools")
def list_pools(profile: Optional[str] = None) -> dict[str, Any]:
    """List pools, optionally filtered by profile.

    Returns masked previews — no raw secrets. Use this to drive the
    Settings > Providers pane in the UI."""
    store = get_store()
    pools = store.list_pools()
    if profile:
        pools = [p for p in pools if p.profile_id == profile]
    return {"pools": [_pool_view(p) for p in pools]}


@router.get("/pools/{provider_id}/{profile_id}")
def get_pool(provider_id: str, profile_id: str) -> dict[str, Any]:
    store = get_store()
    pool = store.find_pool(provider_id, profile_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")
    return _pool_view(pool)


@router.post("/pools/{provider_id}/{profile_id}/credentials")
def add_credential(
    provider_id: str,
    profile_id: str,
    body: AddCredentialBody,
) -> dict[str, Any]:
    store = get_store()
    try:
        cred = _build_credential(provider_id, profile_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.add_credential(cred)
    return _credential_view(cred)


@router.delete("/pools/{provider_id}/{profile_id}/credentials/{credential_id}")
def remove_credential(
    provider_id: str,
    profile_id: str,
    credential_id: str,
) -> dict[str, Any]:
    store = get_store()
    # Capture pre-state so we can tell 404 from no-op.
    pool = store.find_pool(provider_id, profile_id)
    if pool is None or not any(c.credential_id == credential_id for c in pool.credentials):
        raise HTTPException(status_code=404, detail="credential not found")
    store.remove_credential(provider_id, profile_id, credential_id)
    return {"removed": credential_id}


def _build_credential(
    provider_id: str,
    profile_id: str,
    body: AddCredentialBody,
) -> Credential:
    """Validate-and-construct a :class:`Credential` from a UI payload.

    We accept only the types the UI can reasonably ask for directly —
    ``api_key``, ``oauth``, ``external_process``. ``cli_delegated``
    comes from the discovery flow, not manual creation;
    ``device_code`` / ``sso`` come from dedicated login flows."""
    kind = body.type
    if kind == "api_key":
        if not body.api_key:
            raise ValueError("api_key is required for type=api_key")
        return Credential(
            provider_id=provider_id,
            profile_id=profile_id,
            kind="api_key",
            payload=CredentialData(kind="api_key", auth_value=body.api_key.strip()),
            source="webui_paste",
            metadata=dict(body.metadata),
        )
    if kind == "oauth":
        if not body.access_token:
            raise ValueError("access_token is required for type=oauth")
        return Credential(
            provider_id=provider_id,
            profile_id=profile_id,
            kind="oauth",
            payload=CredentialData(
                kind="oauth",
                auth_value=body.access_token.strip(),
                data={
                    "refresh_token": (body.refresh_token or "").strip(),
                    "expires_at_ms": body.expires_at_ms or 0,
                    "client_id": (body.client_id or "").strip(),
                },
            ),
            source="webui_paste",
            metadata=dict(body.metadata),
        )
    if kind == "external_process":
        if not body.command:
            raise ValueError("command is required for type=external_process")
        return Credential(
            provider_id=provider_id,
            profile_id=profile_id,
            kind="external_process",
            payload=CredentialData(kind="external_process", data={"command": list(body.command)}),
            source="webui_paste",
            metadata=dict(body.metadata),
        )
    raise ValueError(f"unsupported type: {kind!r}")


# ---------------------------------------------------------------------------
# Discovery — non-destructive scan of external sources
# ---------------------------------------------------------------------------

@router.post("/doctor")
def run_doctor_route() -> dict[str, Any]:
    """Run the credential health diagnostic and return findings.

    Same shape as the CLI's ``providers doctor --json`` output. Used by
    the Settings UI's "Run diagnostic" button. No mutation; safe to
    call repeatedly.
    """
    from openprogram.auth.cli import run_doctor
    return run_doctor()


@router.post("/adopt_all")
def adopt_all_route(profile: Optional[str] = None) -> dict[str, Any]:
    """Batch-adopt every credential discover() finds.

    Server-side equivalent of ``providers adopt --all``. Idempotent —
    existing credentials (keyed by source label) are skipped. Returns a
    structured report with per-adoption events so the UI can render a
    toast per success.
    """
    from openprogram.auth.cli import run_adopt_all
    target_profile = profile or DEFAULT_PROFILE_NAME
    return run_adopt_all(target_profile)


@router.get("/aliases")
def list_aliases_route() -> dict[str, str]:
    """Return the provider-alias table so the UI can render short names
    alongside canonical ids in pickers and tooltips."""
    from openprogram.auth.aliases import known_aliases
    return known_aliases()


@router.post("/discover")
def discover_credentials() -> dict[str, Any]:
    """Scan every registered :class:`CredentialSource` without writing.

    Returns a list of discovered credentials plus the removal-steps
    contract each source would produce on forget, so the UI can preview
    the full lifecycle before the user commits. Writing discovered
    credentials into the store is a separate :func:`adopt` call — this
    endpoint is read-only."""
    from openprogram.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

    pm = get_profile_manager()
    default_profile = pm.get_profile(DEFAULT_PROFILE_NAME)
    profile_root = default_profile.root

    sources = [
        CodexCliSource(),
        QwenCliSource(),
        GhCliSource(),
    ]
    for provider_id, env_var in PROVIDER_ENV_VARS.items():
        sources.append(EnvApiKeySource(provider_id=provider_id, env_var=env_var))

    found: list[dict[str, Any]] = []
    for src in sources:
        try:
            creds = src.try_import(profile_root)
        except Exception as e:
            # One broken source shouldn't poison discovery of the others.
            found.append({
                "source_id": getattr(src, "source_id", src.__class__.__name__),
                "error": str(e),
            })
            continue
        for cred in creds:
            found.append({
                "source_id": src.source_id,
                "credential": _credential_view(cred),
                "removal_steps": [
                    {
                        "description": step.description,
                        "executable": step.executable,
                        "kind": step.kind,
                        "target": step.target,
                    }
                    for step in src.removal_steps(cred)
                ],
            })
    return {"discovered": found}


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------

# Set of active SSE queues. New subscribers append their queue here; the
# store listener fans events out by iterating the set. Using a set rather
# than a list so removing disconnected queues doesn't shift indices under
# concurrent iteration.
# Each subscriber registers ``(queue, loop)`` so the listener can cross
# threads via ``loop.call_soon_threadsafe`` — asyncio.Queue isn't
# thread-safe, and store mutations can happen off the event loop
# (sync contexts, worker threads).
_subscribers: set[tuple[asyncio.Queue[AuthEvent], asyncio.AbstractEventLoop]] = set()
_wired: bool = False


def _wire_store_listener() -> None:
    """Attach a store-level listener that dispatches to SSE queues.

    Idempotent: runs once per process even if :func:`create_app` is
    called multiple times (as in tests). The listener uses
    ``call_soon_threadsafe`` because stores emit from whichever thread
    called :meth:`put_pool` / :meth:`add_credential` — that's often a
    threadpool worker rather than the SSE endpoint's loop.
    """
    global _wired
    if _wired:
        return
    store = get_store()

    def on_event(event: AuthEvent) -> None:
        for queue, loop in list(_subscribers):
            try:
                loop.call_soon_threadsafe(_put_nowait_with_drop, queue, event)
            except RuntimeError:
                # Loop closed — subscriber didn't clean up. Drop it.
                _subscribers.discard((queue, loop))

    store.subscribe(on_event)
    _wired = True


def _put_nowait_with_drop(queue: asyncio.Queue[AuthEvent], event: AuthEvent) -> None:
    """Non-blocking put that drops the oldest event on overflow.

    An SSE subscriber that stalls (slow network, paused tab) must not
    block the store's event emission path. When the queue is full, we
    discard the oldest queued event and push the new one — the UI gets
    slightly degraded history but stays live."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Two racing producers just filled it again; give up on this
            # event rather than loop. The subscriber will see a gap.
            pass


def _event_to_json(event: AuthEvent) -> str:
    """Serialize an :class:`AuthEvent` in the SSE JSON line format."""
    return json.dumps({
        "type": event.type.value,
        "provider_id": event.provider_id,
        "profile_id": event.profile_id,
        "credential_id": event.credential_id,
        "detail": event.detail,
        "timestamp_ms": event.timestamp_ms,
    })


@router.get("/events")
async def event_stream() -> StreamingResponse:
    """SSE endpoint the UI subscribes to for live auth updates.

    Emits one JSON object per line. Heartbeats (`: keepalive\\n\\n`)
    every 15 s so proxies don't cut idle connections."""
    _wire_store_listener()
    queue: asyncio.Queue[AuthEvent] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()
    # Set.add / discard are atomic in CPython; no extra lock is needed
    # and multiple subscribers can register concurrently.
    entry = (queue, loop)
    _subscribers.add(entry)

    async def gen() -> AsyncGenerator[bytes, None]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                yield f"data: {_event_to_json(event)}\n\n".encode("utf-8")
        finally:
            _subscribers.discard(entry)

    return StreamingResponse(gen(), media_type="text/event-stream")


__all__ = ["router"]
