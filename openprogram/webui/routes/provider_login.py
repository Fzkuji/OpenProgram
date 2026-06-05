"""Unified provider-login session endpoints (P1 step 3).

Drives the surface-agnostic login driver (``openprogram/auth/login_driver``)
from the web UI — and, by reusing these same endpoints, the TUI. A
``_RemoteLoginUi`` forwards the driver's ``open_url`` / ``prompt`` /
``show_progress`` / ``show_code`` interactions into a polled session; the
frontend renders them and posts back any code/key the driver asks for. It's the
same flow the CLI runs in the terminal, now reachable from the browser, so every
provider's native login works from every surface instead of "go use the other
one".

Shape (mirrors the proven claude-code accounts start/submit pattern, but generic
over any provider/method):

  POST /api/providers/{name}/login/start   {method?, profile?, api_key?}
      -> {session, method}; spawns the login coroutine in the background.
  GET  /api/providers/{name}/login/poll?session=&cursor=
      -> {events[], cursor, waiting, prompt, done, ok, error, name}
  POST /api/providers/{name}/login/submit  {session, value}
      -> {ok}; resolves the driver's pending prompt with `value`.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from fastapi.responses import JSONResponse

# session_id -> _LoginSession
_SESSIONS: dict[str, "_LoginSession"] = {}
_SESSION_TTL = 600.0  # abandoned sessions are reaped after 10 min


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
            return await fut
        finally:
            self._s.pending = None


def _reap() -> None:
    """Kill + drop sessions the user started but abandoned (no submit), so the
    coroutine awaiting a prompt future doesn't leak."""
    now = time.time()
    for sid in list(_SESSIONS):
        s = _SESSIONS.get(sid)
        if not s or (not s.done and now - s.started_at <= _SESSION_TTL):
            continue
        if s.task and not s.task.done():
            s.task.cancel()
        _SESSIONS.pop(sid, None)


def register(app):
    @app.post("/api/providers/{name}/login/start")
    async def login_start(name: str, body: dict = None):
        _reap()
        b = body or {}
        profile = (b.get("profile") or "default").strip() or "default"
        api_key = b.get("api_key")
        method = (b.get("method") or "").strip()
        if not method:
            from openprogram.auth.login_methods import default_method
            method = default_method(name)

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

        sess.task = asyncio.create_task(_drive())
        return JSONResponse(content={"session": sid, "method": method})

    @app.get("/api/providers/{name}/login/poll")
    async def login_poll(name: str, session: str = "", cursor: int = 0):
        sess = _SESSIONS.get(session)
        if not sess:
            return JSONResponse(content={"error": "no such login session"}, status_code=404)
        out = {
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
        }
        if sess.done:
            _SESSIONS.pop(session, None)
        return JSONResponse(content=out)

    @app.post("/api/providers/{name}/login/submit")
    async def login_submit(name: str, body: dict = None):
        b = body or {}
        sess = _SESSIONS.get((b.get("session") or "").strip())
        if not sess or not sess.pending:
            return JSONResponse(content={"error": "no pending prompt"}, status_code=409)
        fut = sess.pending["future"]
        if not fut.done():
            fut.set_result(str(b.get("value", "")))
        return JSONResponse(content={"ok": True})
