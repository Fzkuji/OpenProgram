"""agent_browser tool — drive a browser through the npm ``agent-browser`` CLI.

Sister tool to ``tools/browser`` (Playwright-direct). The npm
``agent-browser`` package is purpose-built for LLM agents:

  - ``snapshot`` returns a YAML accessibility tree (ariaSnapshot)
  - every interactive element gets a stable ``@e<N>`` ref id
  - ``click @e3`` / ``type @e7 "hello"`` operate by ref id, not CSS

This is the abstraction Hermes / Browser Use Cloud / Browserbase agents
all consume. Useful when the LLM should reason about pages
semantically (button "Submit", textbox "Email") instead of fishing CSS
selectors out of class soup.

Backends:
  - **local**: agent-browser launches a headless Chromium; one session
    per task id, generated automatically.
  - **cdp**: when ``OPENPROGRAM_BROWSER_CDP_URL`` is set or the
    sidecar Chrome started by the playwright tool is running on
    localhost:9222, we attach via ``--cdp`` so this tool drives the
    same logged-in Chrome.

Setup:
  npm install -g agent-browser
  agent-browser install        # downloads chromium

Usage:
  result = execute(action="navigate", url="https://example.com")
  result = execute(action="snapshot")
  result = execute(action="click", ref="@e3")
  result = execute(action="type", ref="@e7", text="hello")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from typing import Any, Optional


NAME = "agent_browser"

DESCRIPTION = (
    "Drive a browser via the npm `agent-browser` CLI. ariaSnapshot + ref-id "
    "(@e1, @e2) interface designed for LLM agents — no CSS selectors. "
    "11 verbs (open / navigate / snapshot / click / type / scroll / press / "
    "back / console / list / close). Sister tool: `playwright_browser` "
    "exposes the same browser via raw Playwright + CSS selectors when DOM "
    "control matters. Auto-attaches to the playwright_browser sidecar "
    "Chrome via CDP when running, so both tools share one logged-in "
    "browser. Setup: `npm i -g agent-browser && agent-browser install`."
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open", "navigate", "snapshot", "click", "type",
                    "scroll", "press", "back", "console", "close", "list",
                ],
                "description": "What to do.",
            },
            "session_id": {
                "type": "string",
                "description": "Session id returned by `open`. Required for every action except `open` / `list`.",
            },
            "url": {
                "type": "string",
                "description": "URL for `navigate`.",
            },
            "ref": {
                "type": "string",
                "description": "Element ref id (e.g. `@e3`) for `click` / `type` / `press`. Get from `snapshot`.",
            },
            "text": {
                "type": "string",
                "description": "Text to type for `type`.",
            },
            "submit": {
                "type": "boolean",
                "description": "Press Enter after typing (default false).",
            },
            "key": {
                "type": "string",
                "description": "Keyboard key for `press` (e.g. 'Enter', 'Escape', 'ArrowDown').",
            },
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll for `scroll` (positive = down). Default 500.",
            },
            "cdp_url": {
                "type": "string",
                "description": "For `open`: connect to a running Chrome via CDP (e.g. http://localhost:9222) instead of launching a local headless one. Auto-detected from the playwright sidecar Chrome if running.",
            },
        },
        "required": ["action"],
    },
}


# Per-process session table. Each entry holds the session_name passed to
# agent-browser via --session (or --cdp) so subsequent verbs reach the
# same browser instance.
_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def check_agent_browser() -> bool:
    """Gate the tool's visibility by whether `agent-browser` is callable."""
    if shutil.which("agent-browser"):
        return True
    if shutil.which("npx"):
        # Best-effort probe: trust npx to fetch the package on first run.
        return True
    return False


def _install_hint() -> str:
    return (
        "Error: agent-browser not installed. Setup:\n"
        "  npm install -g agent-browser\n"
        "  agent-browser install"
    )


def _resolve_binary() -> str:
    """Prefer the globally installed `agent-browser` binary; fall back to npx."""
    direct = shutil.which("agent-browser")
    if direct:
        return direct
    return "npx agent-browser"


def _detect_sidecar_cdp() -> Optional[str]:
    """If the playwright sidecar Chrome is running, return its CDP URL.

    Lets agent-browser drive the same logged-in Chrome the playwright
    tool uses, so an agent can mix-and-match the two surfaces.
    """
    try:
        from openprogram.tools.browser._chrome_bootstrap import (
            cdp_url_if_available,
        )
        return cdp_url_if_available()
    except Exception:
        return None


def _build_cmd(
    binary: str,
    backend: list[str],
    command: str,
    args: list[str],
) -> list[str]:
    """agent-browser invocation: <bin> <backend> --json <cmd> <args...>"""
    head = ["npx", "agent-browser"] if binary == "npx agent-browser" else [binary]
    return head + backend + ["--json", command] + args


def _run(
    sess: dict[str, Any],
    command: str,
    args: list[str],
    timeout_s: int = 60,
) -> str:
    binary = _resolve_binary()
    backend = sess.get("backend") or []
    cmd = _build_cmd(binary, backend, command, args)
    try:
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return f"Error: agent-browser timed out after {timeout_s}s on `{command}`."
    except FileNotFoundError:
        return _install_hint()
    if out.returncode != 0:
        err = (out.stderr or b"").decode("utf-8", errors="replace").strip()
        return f"Error: agent-browser exited {out.returncode}: {err[:500]}"
    text = (out.stdout or b"").decode("utf-8", errors="replace").strip()
    # Try to pretty-print JSON, otherwise return raw.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        pass
    if len(text) > 8000:
        text = text[:8000] + f"\n\n[truncated, {len(text) - 8000} more chars]"
    return text


def _require_session(session_id: str) -> Any:
    if not session_id:
        return "Error: `session_id` is required."
    sess = _sessions.get(session_id)
    if sess is None:
        return f"Error: no agent_browser session with id {session_id!r}."
    return sess


def _open(*, cdp_url: Optional[str] = None) -> str:
    if not check_agent_browser():
        return _install_hint()
    # Auto-pick CDP from the sidecar Chrome if user hasn't passed one.
    if not cdp_url:
        cdp_url = os.environ.get("OPENPROGRAM_BROWSER_CDP_URL") or _detect_sidecar_cdp()
    session_id = "ab_" + uuid.uuid4().hex[:10]
    if cdp_url:
        backend = ["--cdp", cdp_url]
        mode = f"cdp ({cdp_url})"
    else:
        session_name = f"openprogram-{session_id}"
        backend = ["--session", session_name]
        mode = f"local (--session {session_name})"
    with _sessions_lock:
        _sessions[session_id] = {
            "backend": backend,
            "session_name": backend[1] if backend[0] == "--session" else None,
            "cdp_url": cdp_url,
            "created_at": __import__("time").time(),
        }
    return (
        f"Opened agent_browser session `{session_id}` ({mode}). "
        f"Pass this id to navigate / snapshot / click / type / scroll / press / console / close."
    )


def _navigate(session_id: str, url: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not url:
        return "Error: `url` is required for navigate."
    return _run(sess, "navigate", [url])


def _snapshot(session_id: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    return _run(sess, "snapshot", [])


def _click(session_id: str, ref: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not ref:
        return "Error: `ref` is required for click (e.g. `@e3` from snapshot)."
    return _run(sess, "click", [ref])


def _type(session_id: str, ref: str, text: str, submit: bool) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not ref:
        return "Error: `ref` is required for type."
    if text is None:
        return "Error: `text` is required for type."
    args = [ref, text]
    if submit:
        args.append("--submit")
    return _run(sess, "type", args)


def _scroll(session_id: str, amount: int) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    return _run(sess, "scroll", [str(amount)])


def _press(session_id: str, key: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not key:
        return "Error: `key` is required for press."
    return _run(sess, "press", [key])


def _back(session_id: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    return _run(sess, "back", [])


def _console(session_id: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    return _run(sess, "console", [])


def _close(session_id: str) -> str:
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess is None:
        return f"Error: no agent_browser session with id {session_id!r}."
    # Local sessions can be killed by name. CDP sessions: leave Chrome alone.
    name = sess.get("session_name")
    if name:
        binary = _resolve_binary()
        cmd = _build_cmd(binary, ["--session", name], "close", [])
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    return f"Closed agent_browser session `{session_id}`."


def _list() -> str:
    with _sessions_lock:
        snap = dict(_sessions)
    if not snap:
        return "(no open agent_browser sessions)"
    lines = [f"Open agent_browser sessions: {len(snap)}"]
    for sid, s in snap.items():
        mode = s.get("cdp_url") or s.get("session_name") or "?"
        lines.append(f"  {sid}  {mode}")
    return "\n".join(lines)


def execute(
    action: Optional[str] = None,
    session_id: Optional[str] = None,
    url: Optional[str] = None,
    ref: Optional[str] = None,
    text: Optional[str] = None,
    submit: bool = False,
    key: Optional[str] = None,
    amount: Optional[int] = None,
    cdp_url: Optional[str] = None,
    **kw: Any,
) -> str:
    if not action:
        return "Error: `action` is required."
    action = action.lower()

    # Dispatch.
    if action == "open":
        return _open(cdp_url=cdp_url)
    if action == "list":
        return _list()
    if action == "navigate":
        return _navigate(session_id or "", url or "")
    if action == "snapshot":
        return _snapshot(session_id or "")
    if action == "click":
        return _click(session_id or "", ref or "")
    if action == "type":
        return _type(session_id or "", ref or "", text or "", submit=submit)
    if action == "scroll":
        return _scroll(session_id or "", amount if amount is not None else 500)
    if action == "press":
        return _press(session_id or "", key or "")
    if action == "back":
        return _back(session_id or "")
    if action == "console":
        return _console(session_id or "")
    if action == "close":
        return _close(session_id or "")
    return f"Error: unknown action {action!r}."


__all__ = ["NAME", "SPEC", "DESCRIPTION", "execute", "check_agent_browser"]
