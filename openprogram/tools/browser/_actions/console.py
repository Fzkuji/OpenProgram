"""Console + frame + request-block actions."""
from __future__ import annotations


_console_buffers: dict[str, list[dict]] = {}


def _console_subscribe(sess: dict, session_id: str) -> None:
    """Lazily attach a console listener to the active page."""
    if sess.get("_console_attached"):
        return
    page = sess["page"]
    buf: list[dict] = []
    _console_buffers[session_id] = buf

    def _handler(msg) -> None:
        try:
            buf.append({
                "type": msg.type,
                "text": msg.text[:500],
            })
            if len(buf) > 200:
                del buf[: len(buf) - 200]
        except Exception:
            pass

    page.on("console", _handler)
    sess["_console_attached"] = True


def _console(session_id: str) -> str:
    """Return any console.log/warn/error captured since session start."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    _console_subscribe(sess, session_id)
    buf = _console_buffers.get(session_id) or []
    if not buf:
        return "(no console output captured yet)"
    lines = [f"  [{m.get('type','log')}] {m.get('text','')}" for m in buf[-50:]]
    return "Console (last 50):\n" + "\n".join(lines)


def _block(session_id: str, selector: str) -> str:
    """Block requests by URL pattern. `selector` is the URL glob."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    page = sess["page"]
    try:
        if not selector or selector == "*":
            page.unroute("**/*")
            return "Cleared all request blocks."
        page.route(selector, lambda route: route.abort())
        return f"Blocking requests matching `{selector}`."
    except Exception as e:
        return f"Error setting block route: {type(e).__name__}: {e}"


def _frames(session_id: str) -> str:
    """List the iframe tree of the active page."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    page = sess["page"]
    try:
        lines: list[str] = []
        for f in page.frames:
            indent = "  " * (len(f.url.split("/")) - 3 if f.parent_frame else 0)
            tag = "main" if f == page.main_frame else f.name or "(no name)"
            lines.append(f"{indent}- {tag}  {f.url}")
        return "\n".join(lines) or "(no frames)"
    except Exception as e:
        return f"Error listing frames: {type(e).__name__}: {e}"


def _frame_eval(session_id: str, selector: str, code: str) -> str:
    """Run JS inside a specific iframe identified by name or URL substring."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for frame_eval (frame name or URL substring)."
    if not code:
        return "Error: `code` is required for frame_eval."
    page = sess["page"]
    try:
        frame = next(
            (f for f in page.frames
             if f.name == selector or selector in (f.url or "")),
            None,
        )
        if frame is None:
            return f"(no frame matching `{selector}`)"
        result = frame.evaluate(code)
        text = str(result)
        if len(text) > 2000:
            text = text[:2000] + f"\n\n[truncated, {len(text) - 2000} more chars]"
        return text
    except Exception as e:
        return f"Error in frame_eval: {type(e).__name__}: {e}"
