"""REST + SSE routes for credential diagnostics, adoption and live events.

  * ``POST /doctor`` — the credential health report
  * ``POST /adopt_all`` / ``POST /discover`` — sign-ins other CLIs hold on
    this machine (Codex, Qwen, GitHub), adopted or only previewed
  * ``GET /aliases`` — the provider alias table
  * ``GET /events`` — :class:`AuthEvent` broadcasts as Server-Sent Events

A provider's list itself is served by ``provider_credentials.py``
(``/api/providers/{id}/credentials``). Secrets are masked on every read.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from openprogram.models.auth.store import get_store
from openprogram.models.auth.types import AuthEvent, Credential


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
    if payload.kind == "import":
        return {
            "type": "import",
            "store_path": payload.data.get("store_path"),
            "access_key_path": payload.data.get("access_key_path"),
        }
    if payload.kind == "credential_process":
        return {
            "type": "credential_process",
            "command": payload.data.get("command"),
            "cache_seconds": payload.data.get("cache_seconds"),
        }
    if payload.kind == "sso":
        return {"type": "sso", "broker": payload.data.get("broker")}
    return {"type": cred.kind, "unknown": True}


def _credential_view(cred: Credential) -> dict[str, Any]:
    return {
        "id": cred.id,
        "kind": cred.kind,
        "provider_id": cred.provider_id,
        "label": cred.display_label(),
        "identity": dict(cred.identity),
        "status": cred.status,
        "source": cred.source,
        "read_only": cred.read_only,
        "payload": _payload_preview(cred),
    }


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
    from openprogram.models.auth.doctor import run_doctor
    return run_doctor()


@router.post("/adopt_all")
def adopt_all_route() -> dict[str, Any]:
    """Batch-adopt every credential discover() finds.

    Server-side equivalent of ``providers adopt --all``. Idempotent —
    a credential already held is skipped. Returns a
    structured report with per-adoption events so the UI can render a
    toast per success.
    """
    from openprogram.models.auth.adoption import run_adopt_all
    return run_adopt_all()


@router.get("/aliases")
def list_aliases_route() -> dict[str, str]:
    """Return the provider-alias table so the UI can render short names
    alongside canonical ids in pickers and tooltips."""
    from openprogram.models.auth.account.aliases import known_aliases
    return known_aliases()


@router.post("/discover")
def discover_credentials() -> dict[str, Any]:
    """Scan every registered :class:`CredentialSource` without writing.

    Returns a list of discovered credentials plus the removal-steps
    contract each source would produce on forget, so the UI can preview
    the full lifecycle before the user commits. Writing discovered
    credentials into the store is a separate :func:`adopt` call — this
    endpoint is read-only."""
    from openprogram.models.auth.adoption import _sources

    sources = _sources()
    account_root = get_store().root

    found: list[dict[str, Any]] = []
    for src in sources:
        try:
            creds = src.try_import(account_root)
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
    called :meth:`put` / :meth:`add_credential` — that's often a
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
