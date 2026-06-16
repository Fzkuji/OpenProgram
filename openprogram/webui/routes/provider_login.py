"""Unified provider-login session endpoints (P1 step 3).

Drives the surface-agnostic login driver (``openprogram/auth/login_driver``)
from the web UI — and, by reusing these same endpoints, the TUI. A
``_RemoteLoginUi`` forwards the driver's ``open_url`` / ``prompt`` /
``show_progress`` / ``show_code`` interactions into a polled session; the
frontend renders them and posts back any code/key the driver asks for. It's the
same flow the CLI runs in the terminal, now reachable from the browser, so every
provider's native login works from every surface instead of "go use the other
one".

Shape:
  POST /api/providers/{name}/login/start   {method?, profile?, api_key?}
      -> {session, method}; spawns the login coroutine in the background.
  GET  /api/providers/{name}/login/poll?session=&cursor=
      -> {events[], cursor, waiting, prompt, done, ok, error, name}
  POST /api/providers/{name}/login/submit  {session, value}
      -> {ok}; resolves the driver's pending prompt with `value`.

`poll` is an idempotent read: it never destroys the session, so a final or
concurrent poll always reads the sticky terminal {done, ok, name} instead of
404-ing and clobbering a just-succeeded login. Sessions are cleaned by `_reap`
(run on every start AND every poll): done sessions after a short grace, and
abandoned ones after a TTL — and an abandoned prompt self-terminates because the
prompt wait is bounded by `asyncio.wait_for`.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from fastapi.responses import JSONResponse

# session_id -> _LoginSession
_SESSIONS: dict[str, "_LoginSession"] = {}
_SESSION_TTL = 600.0   # abandoned (never-finished) sessions reaped after 10 min
_DONE_GRACE = 60.0     # finished sessions kept this long so late polls read them


class _LoginSession:
    def __init__(self) -> None:
        self.events: list[dict] = []        # ordered UI events for the frontend
        self.pending: dict | None = None    # {message, secret, future} while prompting
        self.done: bool = False
        self.ok: bool = False
        self.error: str | None = None
        self.name: str | None = None        # saved profile id on success
        self.task: asyncio.Task | None = None
        self.started_at: float = time.time()
        self.done_at: float | None = None


class _RemoteLoginUi:
    """LoginUi that forwards the driver's interactions to a polled session."""

    def __init__(self, sess: _LoginSession) -> None:
        self._s = sess

    async def open_url(self, url: str) -> None:
        self._s.events.append({"type": "open_url", "url": url})

    async def show_progress(self, message: str) -> None:
        self._s.events.append({"type": "progress", "message": message})

    async def show_code(self, user_code: str, verification_uri: str) -> None:
        self._s.events.append(
            {"type": "code", "user_code": user_code, "verification_uri": verification_uri}
        )

    async def prompt(self, message: str, *, secret: bool = False) -> str:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._s.pending = {"message": message, "secret": secret, "future": fut}
        self._s.events.append({"type": "prompt", "message": message, "secret": secret})
        try:
            # Bound the wait so an abandoned prompt (tab closed, never submitted)
            # self-terminates instead of awaiting a future that never resolves.
            return await asyncio.wait_for(fut, timeout=_SESSION_TTL)
        finally:
            self._s.pending = None


def _free_profile(provider: str) -> str:
    """Pick a profile id for a NEW account that no existing credential occupies.

    First account → "default"; thereafter "account-2", "account-3", … The pool
    is the credential pool id (claude-code's credentials live under
    `anthropic`), so we check occupancy in the right place.
    """
    try:
        from openprogram.webui.routes.accounts import _pool_id
        from openprogram.auth.store import get_store
        pool = _pool_id(provider)
        taken = {
            p.profile_id for p in get_store().list_pools()
            if p.provider_id == pool and p.credentials
        }
    except Exception:
        return "default"
    if "default" not in taken:
        return "default"
    i = 2
    while f"account-{i}" in taken:
        i += 1
    return f"account-{i}"


def _reap() -> None:
    """Drop finished sessions after a grace window and abandoned ones after the
    TTL (cancelling their still-running task). Idempotent; called on every
    start and poll so cleanup never depends on a later request arriving."""
    now = time.time()
    for sid in list(_SESSIONS):
        s = _SESSIONS.get(sid)
        if not s:
            continue
        expired = (
            (s.done and now - (s.done_at or s.started_at) > _DONE_GRACE)
            or (not s.done and now - s.started_at > _SESSION_TTL)
        )
        if not expired:
            continue
        if s.task and not s.task.done():
            s.task.cancel()
        _SESSIONS.pop(sid, None)


def register(app):
    @app.get("/api/providers/{name}/login/methods")
    async def login_methods_list(name: str):
        from openprogram.auth.login_methods import login_methods
        methods = login_methods(name)
        return JSONResponse(content={
            "methods": [{"id": mid, "label": label} for mid, label in methods],
            "default": methods[0][0] if methods else None,
        })

    @app.post("/api/providers/{name}/login/start")
    async def login_start(name: str, body: dict = None):
        _reap()
        # Resolve aliases (e.g. "codex" -> "openai-codex") so the driver gets a
        # canonical id, mirroring the CLI. Best-effort.
        try:
            from openprogram.auth.aliases import resolve as _resolve
            name = _resolve(name) or name
        except Exception:
            pass
        b = body or {}
        explicit_profile = (b.get("profile") or "").strip()
        api_key = b.get("api_key")
        method = (b.get("method") or "").strip()
        if not method:
            from openprogram.auth.login_methods import default_method
            method = default_method(name)

        # An account == a profile == one credential. If the user named the
        # account, honour it; otherwise pick a profile that ISN'T already
        # occupied, so a new sign-in never lands in a profile that already
        # holds a credential (which would hide it behind the existing one in
        # the UI and make the runtime resolve the wrong credential). The pool
        # is keyed by the credential pool id (claude-code shares `anthropic`).
        profile = explicit_profile or _free_profile(name)

        sess = _LoginSession()
        sid = secrets.token_hex(8)
        _SESSIONS[sid] = sess

        async def _drive() -> None:
            from openprogram.auth.login_driver import run_login, persist
            try:
                cred = await run_login(name, profile, method, _RemoteLoginUi(sess), api_key=api_key)
                persist(cred)
                sess.name = getattr(cred, "profile_id", profile)
                sess.ok = True
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                sess.error = f"{e.__class__.__name__}: {e}"
            finally:
                sess.done = True
                sess.done_at = time.time()

        sess.task = asyncio.create_task(_drive())
        return JSONResponse(content={"session": sid, "method": method})

    @app.get("/api/providers/{name}/login/poll")
    async def login_poll(name: str, session: str = "", cursor: int = 0):
        _reap()
        sess = _SESSIONS.get(session)
        if not sess:
            return JSONResponse(content={"error": "no such login session"}, status_code=404)
        # Idempotent read — never pop here; `done` is sticky and a final/
        # concurrent poll must still read the terminal result. _reap cleans up.
        return JSONResponse(content={
            "events": sess.events[cursor:],
            "cursor": len(sess.events),
            "waiting": bool(sess.pending),
            "prompt": (
                {"message": sess.pending["message"], "secret": sess.pending["secret"]}
                if sess.pending else None
            ),
            "done": sess.done,
            "ok": sess.ok,
            "error": sess.error,
            "name": sess.name,
        })

    @app.post("/api/providers/{name}/login/submit")
    async def login_submit(name: str, body: dict = None):
        b = body or {}
        sess = _SESSIONS.get((b.get("session") or "").strip())
        if not sess or not sess.pending:
            return JSONResponse(content={"error": "no pending prompt"}, status_code=409)
        fut = sess.pending["future"]
        accepted = not fut.done()
        if accepted:
            fut.set_result(str(b.get("value", "")))
        # Report whether the value was actually taken (vs a redundant/late submit).
        return JSONResponse(content={"ok": accepted})

    @app.post("/api/providers/{name}/login/cancel")
    async def login_cancel(name: str, body: dict = None):
        b = body or {}
        sid = (b.get("session") or "").strip()
        sess = _SESSIONS.pop(sid, None)
        if sess and sess.task and not sess.task.done():
            sess.task.cancel()
        return JSONResponse(content={"ok": True})
