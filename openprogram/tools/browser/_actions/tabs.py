"""Tab management + download + viewport actions."""
from __future__ import annotations


def _tabs(session_id: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    pages = sess.get("pages") or [sess.get("page")]
    active = sess.get("active", 0)
    lines = [f"Tabs (active = {active}):"]
    for i, p in enumerate(pages):
        try:
            url = p.url
            title = p.title()
        except Exception:
            url, title = "?", "?"
        marker = "→" if i == active else " "
        lines.append(f"  {marker} [{i}]  {title!r}  {url}")
    return "\n".join(lines)


def _new_tab(session_id: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    try:
        page = sess["context"].new_page()
        page.set_default_timeout(sess.get("default_timeout", 30_000))
        sess.setdefault("pages", []).append(page)
        sess["active"] = len(sess["pages"]) - 1
        sess["page"] = page
        return f"Opened tab [{sess['active']}], now active."
    except Exception as e:
        return f"Error opening tab: {type(e).__name__}: {e}"


def _switch_tab(session_id: str, tab_index: int) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    pages = sess.get("pages") or []
    if not (0 <= tab_index < len(pages)):
        return f"Error: tab_index {tab_index} out of range (have {len(pages)} tab(s))."
    sess["active"] = tab_index
    sess["page"] = pages[tab_index]
    try:
        url = sess["page"].url
    except Exception:
        url = "?"
    return f"Switched to tab [{tab_index}] ({url})."


def _download(session_id: str, selector: str, path: str, timeout_ms: int) -> str:
    """Click a selector and capture the resulting download to `path`."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for download (the trigger element)."
    if not path:
        return "Error: `path` is required for download (where to save the file)."
    import os
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    try:
        page = sess["page"]
        with page.expect_download(timeout=timeout_ms) as info:
            page.click(selector)
        dl = info.value
        dl.save_as(path)
        return f"Downloaded {dl.suggested_filename!r} → {path}"
    except Exception as e:
        return f"Error downloading: {type(e).__name__}: {e}"


def _viewport(session_id: str, width: int, height: int) -> str:
    """Resize the active page's viewport (mobile emulation, etc.)."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not (width and height):
        return "Error: width and height required (e.g. width=375 height=812 for iPhone X)."
    try:
        sess["page"].set_viewport_size({"width": int(width), "height": int(height)})
        return f"Viewport set to {width}x{height}."
    except Exception as e:
        return f"Error setting viewport: {type(e).__name__}: {e}"
