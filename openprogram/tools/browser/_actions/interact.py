"""Page-interaction actions: navigate / click / type / hover / select /
press / upload / wait / eval. Stateless on top of an existing session."""
from __future__ import annotations


def _navigate(session_id: str, url: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not url:
        return "Error: `url` is required for navigate."
    try:
        sess["page"].goto(url)
        return f"Navigated {session_id} → {url}\nTitle: {sess['page'].title()}"
    except Exception as e:
        return f"Error navigating: {type(e).__name__}: {e}"


def _click(session_id: str, selector: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for click."
    try:
        sess["page"].click(selector)
        return f"Clicked `{selector}` in {session_id}."
    except Exception as e:
        return f"Error clicking: {type(e).__name__}: {e}"


def _type(session_id: str, selector: str, text: str, submit: bool = False) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for type."
    if text is None:
        return "Error: `text` is required for type."
    try:
        sess["page"].fill(selector, text)
        if submit:
            sess["page"].press(selector, "Enter")
        return f"Typed {len(text)} char(s) into `{selector}`{' + submitted' if submit else ''}."
    except Exception as e:
        return f"Error typing: {type(e).__name__}: {e}"


def _hover(session_id: str, selector: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for hover."
    try:
        sess["page"].hover(selector)
        return f"Hovered `{selector}`."
    except Exception as e:
        return f"Error hovering: {type(e).__name__}: {e}"


def _select_option(session_id: str, selector: str, value: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for select."
    if not value:
        return "Error: `value` is required for select."
    try:
        values = [v.strip() for v in value.split(",")] if "," in value else value
        sess["page"].select_option(selector, values)
        return f"Selected `{value}` in `{selector}`."
    except Exception as e:
        return f"Error selecting: {type(e).__name__}: {e}"


def _press(session_id: str, key: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not key:
        return "Error: `key` is required for press (e.g. 'Enter', 'Escape', 'Control+A')."
    try:
        sess["page"].keyboard.press(key)
        return f"Pressed `{key}`."
    except Exception as e:
        return f"Error pressing key: {type(e).__name__}: {e}"


def _upload(session_id: str, selector: str, path: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for upload (target the <input type=file>)."
    if not path:
        return "Error: `path` is required for upload."
    import os
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        sess["page"].set_input_files(selector, path)
        return f"Uploaded `{path}` → `{selector}`."
    except Exception as e:
        return f"Error uploading: {type(e).__name__}: {e}"


def _wait(
    session_id: str,
    selector: str | None,
    state: str | None,
    timeout_ms: int,
) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    page = sess["page"]
    try:
        if selector:
            element_state = state or "visible"
            if element_state not in ("attached", "detached", "visible", "hidden"):
                return (
                    f"Error: with a selector, `state` must be attached / detached / "
                    f"visible / hidden (got {element_state!r})."
                )
            page.wait_for_selector(selector, state=element_state, timeout=timeout_ms)
            return f"Element `{selector}` reached state `{element_state}`."
        page_state = state or "networkidle"
        if page_state not in ("load", "domcontentloaded", "networkidle"):
            return (
                f"Error: without a selector, `state` must be load / domcontentloaded / "
                f"networkidle (got {page_state!r})."
            )
        page.wait_for_load_state(page_state, timeout=timeout_ms)
        return f"Page reached state `{page_state}`."
    except Exception as e:
        return f"Error waiting: {type(e).__name__}: {e}"


def _eval_js(session_id: str, code: str) -> str:
    from openprogram.tools.browser import browser as _b
    sess = _b._require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not code:
        return "Error: `code` is required for eval."
    try:
        result = sess["page"].evaluate(code)
        if isinstance(result, (dict, list)):
            import json as _json
            text = _json.dumps(result, default=str, ensure_ascii=False)
        else:
            text = str(result)
        if len(text) > 2000:
            text = text[:2000] + f"\n\n[truncated, {len(text) - 2000} more chars]"
        return text
    except Exception as e:
        return f"Error evaluating: {type(e).__name__}: {e}"
