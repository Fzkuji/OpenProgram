"""Session lifecycle actions: save_login / close / list."""
from __future__ import annotations


def _save_login(session_id: str, name: str | None = None) -> str:
    """Snapshot the session's storage_state for later headless reuse."""
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    target = name or sess.get("login_url") or ""
    if not target:
        return (
            "Error: pass `name` (host or url) — couldn't infer one from the session."
        )
    path = _b._state_path_for(target)
    try:
        sess["context"].storage_state(path=path)
        return (
            f"Saved login for `{target}` → {path}\n"
            f"  Future open(url='https://{target.lstrip('https://')}/...') "
            f"calls will load this automatically and run headless."
        )
    except Exception as e:
        return f"Error saving login: {type(e).__name__}: {e}"


def _close(session_id: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._sessions.pop(session_id, None)
    if sess is None:
        return f"Error: no browser session with id {session_id!r}."
    errors: list[str] = []
    is_cdp = sess.get("is_cdp", False)
    if is_cdp:
        # Just close our own page — never tear down the user's real Chrome.
        page = sess.get("page")
        if page is not None:
            try:
                page.close()
            except Exception as e:
                errors.append(f"page: {e}")
        pw = sess.get("playwright")
        if pw is not None:
            try:
                pw.stop()
            except Exception as e:
                errors.append(f"playwright: {e}")
        if errors:
            return f"Detached from Chrome (session {session_id}) with warnings: {'; '.join(errors)}"
        return f"Detached from Chrome (session {session_id}). Your Chrome window stays open."
    for key in ("context", "browser"):
        obj = sess.get(key)
        if obj is None:
            continue
        try:
            obj.close()
        except Exception as e:
            errors.append(f"{key}: {e}")
    pw = sess.get("playwright")
    if pw is not None:
        try:
            pw.stop()
        except Exception as e:
            errors.append(f"playwright: {e}")
    if errors:
        return f"Closed {session_id} with warnings: {'; '.join(errors)}"
    return f"Closed browser session `{session_id}`."


def _list() -> str:
    from openprogram.tools.browser import browser as _b
    if not _b._sessions:
        return "(no open browser sessions)"
    lines = [f"Open browser sessions: {len(_b._sessions)}"]
    for sid, sess in _b._sessions.items():
        try:
            url = sess["page"].url
        except Exception:
            url = "?"
        lines.append(f"  {sid}  {url}")
    return "\n".join(lines)
