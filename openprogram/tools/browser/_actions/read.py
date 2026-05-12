"""Page-read actions: extract / html / accessibility / screenshot /
screenshot_b64 / cookies. All return short string previews capped to
keep responses within model context budget."""
from __future__ import annotations


def _extract(session_id: str, selector: str | None = None) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    try:
        page = sess["page"]
        if selector:
            el = page.locator(selector)
            count = el.count()
            if count == 0:
                return f"(no elements matched `{selector}`)"
            pieces: list[str] = []
            for i in range(min(count, 20)):
                pieces.append(el.nth(i).inner_text())
            return f"[{count} match(es) for `{selector}`, up to 20 shown]\n\n" + "\n---\n".join(pieces)
        return page.inner_text("body")
    except Exception as e:
        return f"Error extracting: {type(e).__name__}: {e}"


def _html(session_id: str, selector: str | None) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    page = sess["page"]
    try:
        if selector:
            el = page.locator(selector)
            count = el.count()
            if count == 0:
                return f"(no elements matched `{selector}`)"
            html = el.first.inner_html()
        else:
            html = page.content()
        if len(html) > 5000:
            html = html[:5000] + f"\n\n[truncated, {len(html) - 5000} more chars]"
        return html
    except Exception as e:
        return f"Error fetching HTML: {type(e).__name__}: {e}"


def _accessibility(session_id: str, selector: str | None) -> str:
    """Return a YAML-style aria snapshot of the page or a subtree.

    Useful as an alternative to raw HTML when an LLM needs to find
    interactive elements without parsing CSS selectors out of class soup.
    """
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    try:
        page = sess["page"]
        target = page.locator(selector) if selector else page.locator("body")
        if target.count() == 0:
            return f"(no elements matched `{selector}`)"
        snap = target.first.aria_snapshot()
        if not snap:
            return "(empty accessibility tree)"
        if len(snap) > 8000:
            snap = snap[:8000] + f"\n\n[truncated, {len(snap) - 8000} more chars]"
        return snap
    except Exception as e:
        return f"Error fetching accessibility tree: {type(e).__name__}: {e}"


def _screenshot(session_id: str, path: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not path:
        return "Error: `path` is required for screenshot."
    try:
        sess["page"].screenshot(path=path, full_page=True)
        return f"Saved screenshot → {path}"
    except Exception as e:
        return f"Error taking screenshot: {type(e).__name__}: {e}"


def _screenshot_b64(session_id: str, selector: str | None = None) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    try:
        page = sess["page"]
        if selector:
            el = page.locator(selector)
            if el.count() == 0:
                return f"(no elements matched `{selector}`)"
            buf = el.first.screenshot()
        else:
            buf = page.screenshot(full_page=True)
        import base64
        b64 = base64.b64encode(buf).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        return f"Error taking screenshot: {type(e).__name__}: {e}"


def _cookies(session_id: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    try:
        rows = sess["context"].cookies()
        if not rows:
            return "(no cookies)"
        import json as _json
        text = _json.dumps(rows, default=str, ensure_ascii=False, indent=2)
        if len(text) > 4000:
            text = text[:4000] + f"\n\n[truncated, {len(text) - 4000} more chars]"
        return text
    except Exception as e:
        return f"Error reading cookies: {type(e).__name__}: {e}"
