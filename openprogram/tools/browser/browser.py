"""browser tool — automate a headless Chromium via Playwright.

Verbs:

    open       launch browser + context; returns a session id
    navigate   <session_id, url>                       load a URL
    click      <session_id, selector>                  click an element
    type       <session_id, selector, text, [submit]>  fill an input
    extract    <session_id, [selector]>                return readable text
    screenshot <session_id, path>                      save PNG, return path
    close      <session_id>                            tear down

Sessions are process-local — ``open`` returns an id the agent passes
to subsequent calls. Each session owns one Playwright browser + one
page; multi-page workflows can open multiple sessions.

Playwright is an optional dependency. Without it, ``check_playwright``
returns False so the runtime hides the tool. The first call still
emits a clear install hint if the user forces it anyway.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from .._helpers import read_string_param


NAME = "browser"

DESCRIPTION = (
    "Automate a headless Chromium browser via Playwright. Open a session, "
    "navigate to URLs, click / type into elements by CSS selector, extract "
    "page text, or take screenshots. Sessions persist across tool calls "
    "within the same process — always ``close`` when done. Install the "
    "runtime first: `pip install playwright && playwright install chromium`."
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
                    "open", "navigate", "click", "type",
                    "extract", "screenshot", "close", "list",
                    "upload", "wait", "eval", "html",
                    "hover", "select", "press",
                    "accessibility", "screenshot_b64",
                    "tabs", "new_tab", "switch_tab",
                    "download", "cookies",
                    "save_login",
                ],
                "description": "What to do.",
            },
            "session_id": {
                "type": "string",
                "description": "Session id returned by ``open``. Required for every action except ``open`` / ``list``.",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to. Required for ``navigate``.",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector. Required for ``click`` / ``type`` / ``upload``, optional for ``extract`` / ``html`` / ``wait``.",
            },
            "text": {
                "type": "string",
                "description": "Text to type. Required for ``type``.",
            },
            "submit": {
                "type": "boolean",
                "description": "When typing, press Enter afterwards (default false).",
            },
            "path": {
                "type": "string",
                "description": "For ``screenshot``: output PNG path. For ``upload``: file path on disk to attach to the matching <input type=file>.",
            },
            "code": {
                "type": "string",
                "description": "JavaScript expression for ``eval``. Result is JSON-stringified, capped at 2KB.",
            },
            "state": {
                "type": "string",
                "enum": ["attached", "detached", "visible", "hidden", "load", "domcontentloaded", "networkidle"],
                "description": "For ``wait``: with a selector, the element state to wait for (visible/hidden/attached/detached). Without a selector, a page load state (load/domcontentloaded/networkidle). Default 'visible' / 'networkidle'.",
            },
            "key": {
                "type": "string",
                "description": "For ``press``: keyboard key (e.g. 'Enter', 'Escape', 'Control+A', 'ArrowDown').",
            },
            "value": {
                "type": "string",
                "description": "For ``select``: option value to select inside the <select> at `selector`. Pass a list-as-string for multi-select if the element supports it.",
            },
            "tab_index": {
                "type": "integer",
                "description": "For ``switch_tab``: zero-based index returned by ``tabs`` / ``new_tab``.",
            },
            "name": {
                "type": "string",
                "description": "For ``save_login``: optional host or label to file the saved state under. Defaults to the host of the URL the session was opened with.",
            },
            "storage_state": {
                "type": "string",
                "description": "For ``open``: path to a saved storage_state JSON. If omitted but ``url`` is given, the tool auto-loads ``~/.openprogram/browser-states/<host>.json`` if it exists.",
            },
            "headless": {
                "type": "boolean",
                "description": "For ``open``: start browser in headless mode (default true).",
            },
            "stealth": {
                "type": "boolean",
                "description": "For ``open``: apply anti-bot patches (navigator.webdriver, chrome runtime, plugins, WebGL vendor) so Cloudflare/Distil-style detection passes more often (default true).",
            },
            "engine": {
                "type": "string",
                "enum": ["auto", "chromium", "patchright", "camoufox"],
                "description": "For ``open``: which browser backend to use. Default 'auto' connects to a sidecar copy of the user's real Chrome (logged into all their accounts) — first call may take a minute on first run while the profile copies, subsequent calls are instant. Override with 'chromium' (stock Playwright), 'patchright' (stealth Chromium fork — useful when 'auto' Chrome isn't installed), or 'camoufox' (stealth Firefox).",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Per-action timeout in ms (default 30000).",
            },
        },
        "required": ["action"],
    },
}


# Per-process session table. Value shape: dict with playwright, browser, page.
# Kept minimal — we don't persist across process exits.
_sessions: dict[str, dict[str, Any]] = {}


def _state_dir() -> str:
    """Where saved login states live.

    One JSON per host (cookies + localStorage). Created lazily.
    """
    import os
    from pathlib import Path
    p = Path.home() / ".openprogram" / "browser-states"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _state_path_for(name_or_url: str) -> str:
    """Map a host or arbitrary name to the on-disk JSON path."""
    import os
    from urllib.parse import urlparse
    name = name_or_url
    if "://" in name_or_url:
        try:
            name = urlparse(name_or_url).hostname or name_or_url
        except Exception:
            pass
    # Sanitize for filesystem.
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in name)
    return os.path.join(_state_dir(), f"{safe}.json")


def _has_saved_login(name_or_url: str) -> bool:
    import os
    return os.path.isfile(_state_path_for(name_or_url))


def check_playwright() -> bool:
    """Gate the tool's visibility by whether Playwright is importable."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _install_hint() -> str:
    return (
        "Error: Playwright not installed. Run:\n"
        "  pip install playwright\n"
        "  playwright install chromium"
    )


def _require_session(session_id: str) -> dict[str, Any] | str:
    if not session_id:
        return "Error: `session_id` is required."
    sess = _sessions.get(session_id)
    if sess is None:
        return f"Error: no browser session with id {session_id!r}."
    return sess


def _current_page(sess: dict[str, Any]):
    """Return the active page for a session (multi-tab support)."""
    pages = sess.get("pages") or [sess.get("page")]
    idx = sess.get("active", 0)
    return pages[idx] if 0 <= idx < len(pages) else pages[0]


# Init script that patches the most commonly fingerprinted Playwright
# tells. Cloudflare Turnstile / Distil / DataDome use these to flag
# automation. Doesn't make us undetectable — sites with sophisticated
# canvas / WebGL / TLS fingerprinting will still catch us — but
# handles the trivial checks (navigator.webdriver, missing plugins,
# languages, chrome runtime).
_STEALTH_INIT_SCRIPT = """
() => {
  // 1. navigator.webdriver = undefined (default true under automation)
  Object.defineProperty(Navigator.prototype, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });
  // 2. window.chrome (real Chrome has this, headless doesn't)
  if (!window.chrome) {
    window.chrome = { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
  }
  // 3. plugins / mimeTypes — empty arrays in Playwright
  Object.defineProperty(navigator, 'plugins', {
    get: () => [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
    ],
    configurable: true,
  });
  // 4. languages — Playwright sets ['en-US'] by default; en-US is fine
  //    but having the array fall through navigator.language is good
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
    configurable: true,
  });
  // 5. permissions.query — return prompt for notifications instead of denied
  if (window.navigator.permissions) {
    const orig = window.navigator.permissions.query.bind(window.navigator.permissions);
    window.navigator.permissions.query = (params) =>
      params && params.name === 'notifications'
        ? Promise.resolve({ state: 'prompt' })
        : orig(params);
  }
  // 6. WebGL vendor / renderer — common gates
  const getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';                     // VENDOR
    if (p === 37446) return 'Intel Iris OpenGL Engine';       // RENDERER
    return getParam.call(this, p);
  };
}
"""


def _start_engine(engine: str):
    """Start the playwright/patchright/camoufox runtime.

    Returns (pw_instance, browser_kind) where browser_kind is the launcher
    we'll call .launch() on. Falls back to chromium if the requested
    engine isn't installed.
    """
    engine = (engine or "chromium").lower()
    if engine == "patchright":
        try:
            from patchright.sync_api import sync_playwright as _sync_pw
            pw = _sync_pw().start()
            return pw, pw.chromium, "patchright"
        except ImportError:
            return None, None, (
                "Error: patchright not installed. Run:\n"
                "  pip install \"openprogram[browser-stealth]\"\n"
                "  patchright install chromium"
            )
    if engine == "camoufox":
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
            cam = Camoufox(headless=True)  # caller will re-call with kwargs
            return cam, None, "camoufox"
        except ImportError:
            return None, None, (
                "Error: camoufox not installed. Run:\n"
                "  pip install \"openprogram[browser-stealth]\"\n"
                "  camoufox fetch"
            )
    # Default: stock playwright + chromium.
    try:
        from playwright.sync_api import sync_playwright as _sync_pw
        pw = _sync_pw().start()
        return pw, pw.chromium, "chromium"
    except ImportError:
        return None, None, _install_hint()


def _read_cdp_port() -> int | None:
    """If the user ran `openprogram browser attach` we wrote the port here."""
    import os
    from pathlib import Path
    p = Path.home() / ".openprogram" / "browser-cdp-port"
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def _open(
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    stealth: bool = True,
    engine: str = "auto",
    url: str | None = None,
    storage_state: str | None = None,
    cdp_url: str | None = None,
) -> str:
    """Open a browser session, optionally pre-loading a saved login.

    UX flow:
      - If `url` is given AND we already have a saved login for that
        host, load the state and run headless — the agent gets a
        logged-in session with no manual step.
      - If `url` is given but we don't have a saved login, force
        headless=False so the user can log in manually, then prompts
        them to call `save_login` with the returned session id.
      - If `storage_state` is given explicitly, that path overrides the
        host-based lookup.
    """
    if not check_playwright():
        return _install_hint()

    # Auto-bootstrap path (default): when the caller didn't pin a
    # specific engine and didn't pass cdp_url, ensure a sidecar Chrome
    # is running and route through CDP. First call may take a minute
    # because it copies the user's Chrome profile (~3GB) so saved
    # logins / extensions are available; subsequent calls are instant.
    auto_engine = engine in (None, "", "auto")
    if cdp_url is None and auto_engine:
        from openprogram.tools.browser._chrome_bootstrap import (
            cdp_url_if_available, launch_sidecar_chrome,
        )
        cdp_url = cdp_url_if_available()
        if cdp_url is None:
            ok = launch_sidecar_chrome()
            if ok:
                cdp_url = cdp_url_if_available()
        # If bootstrap failed (no Chrome installed, sandbox issues),
        # fall through to plain chromium below.
        if cdp_url is None:
            engine = "chromium"

    # Legacy-explicit path: caller asked for cdp via the auto port file.
    if cdp_url is None and not auto_engine:
        port = _read_cdp_port()
        if port is not None:
            cdp_url = f"http://localhost:{port}"

    if cdp_url:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            # Real Chrome already has a default context with cookies/login.
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            pages = list(context.pages) or [context.new_page()]
            # Use a fresh page so we don't hijack whatever the user has open.
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            if url:
                try:
                    page.goto(url)
                except Exception:
                    pass
            session_id = "br_" + uuid.uuid4().hex[:10]
            _sessions[session_id] = {
                "engine": "cdp",
                "playwright": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "pages": [page],
                "active": 0,
                "default_timeout": timeout_ms,
                "login_url": url,
                "is_cdp": True,           # don't .close() the user's Chrome
            }
            existing_tabs = len(pages)
            return (
                f"Opened browser session `{session_id}` "
                f"(engine=cdp via {cdp_url}, attached to your running Chrome). "
                f"Found {existing_tabs} existing tab(s); created a new tab for this session."
            )
        except Exception as e:
            return (
                f"Error connecting to Chrome at {cdp_url}: {type(e).__name__}: {e}\n"
                f"Did you run `openprogram browser attach` first?"
            )

    pw, kind, name_or_err = _start_engine(engine)
    if pw is None:
        return name_or_err  # error string from _start_engine

    # Decide which storage_state file to load (if any).
    state_path: str | None = None
    auto_login_needed = False
    if storage_state:
        import os
        state_path = (
            os.path.expanduser(storage_state)
            if not os.path.isabs(storage_state)
            else storage_state
        )
        if not os.path.isfile(state_path):
            return f"Error: storage_state file not found: {state_path}"
    elif url and _has_saved_login(url):
        state_path = _state_path_for(url)
    elif url:
        # No saved login for this host — flip to headed so the user can log in.
        if headless:
            headless = False
            auto_login_needed = True
    try:
        if name_or_err == "camoufox":
            # Camoufox manages its own context; everything below is
            # redundant for it.
            cam = pw  # alias for clarity
            cm = cam.__enter__()  # equivalent to `with Camoufox(...) as cm:`
            page = cm.new_page()
            page.set_default_timeout(timeout_ms)
            session_id = "br_" + uuid.uuid4().hex[:10]
            _sessions[session_id] = {
                "engine": "camoufox",
                "playwright": cam,           # camoufox manager
                "browser": cm,               # browser-like
                "context": cm,
                "page": page,
                "pages": [page],
                "active": 0,
                "default_timeout": timeout_ms,
            }
            return (
                f"Opened browser session `{session_id}` "
                f"(engine=camoufox, headless=True, timeout={timeout_ms}ms)."
            )

        # playwright / patchright path (chromium-based)
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ] if stealth else []
        browser = kind.launch(headless=headless, args=launch_args)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "en-US",
        }
        if stealth:
            context_kwargs["user_agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            )
        if state_path:
            context_kwargs["storage_state"] = state_path
        context = browser.new_context(**context_kwargs)
        if stealth and name_or_err == "chromium":
            # patchright already does deep patches; layering ours on top can
            # actually re-introduce detectable inconsistencies, so only
            # apply our init script when running stock chromium.
            context.add_init_script(f"({_STEALTH_INIT_SCRIPT})()")
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        if url:
            try:
                page.goto(url)
            except Exception:
                pass  # leave navigation issues to be reported by `navigate`
        session_id = "br_" + uuid.uuid4().hex[:10]
        _sessions[session_id] = {
            "engine": name_or_err,
            "playwright": pw,
            "browser": browser,
            "context": context,
            "page": page,
            "pages": [page],
            "active": 0,
            "default_timeout": timeout_ms,
            "login_url": url,
        }
        msg = (
            f"Opened browser session `{session_id}` "
            f"(engine={name_or_err}, headless={headless}, "
            f"stealth={stealth}, timeout={timeout_ms}ms)."
        )
        if state_path:
            msg += f"\n  Loaded saved login from {state_path}."
        if auto_login_needed:
            msg += (
                "\n\n  No saved login for this host yet. The browser opened "
                "in headed mode at the URL.\n"
                "  1. Log in manually in the window.\n"
                "  2. Then call save_login (session_id=" + session_id + ").\n"
                "  Future `open(url=...)` calls will pick up the saved state automatically."
            )
        return msg
    except Exception as e:
        return f"Error opening browser: {type(e).__name__}: {e}"


def _save_login(session_id: str, name: str | None = None) -> str:
    """Snapshot the session's storage_state for later headless reuse.

    Default `name` is the host of the URL the session was opened at.
    """
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    target = name or sess.get("login_url") or ""
    if not target:
        return (
            "Error: pass `name` (host or url) — couldn't infer one from the session."
        )
    path = _state_path_for(target)
    try:
        sess["context"].storage_state(path=path)
        return (
            f"Saved login for `{target}` → {path}\n"
            f"  Future open(url='https://{target.lstrip('https://')}/...') "
            f"calls will load this automatically and run headless."
        )
    except Exception as e:
        return f"Error saving login: {type(e).__name__}: {e}"


def _navigate(session_id: str, url: str) -> str:
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
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


def _extract(session_id: str, selector: str | None = None) -> str:
    sess = _require_session(session_id)
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


def _screenshot(session_id: str, path: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not path:
        return "Error: `path` is required for screenshot."
    try:
        sess["page"].screenshot(path=path, full_page=True)
        return f"Saved screenshot → {path}"
    except Exception as e:
        return f"Error taking screenshot: {type(e).__name__}: {e}"


def _upload(session_id: str, selector: str, path: str) -> str:
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    page = sess["page"]
    try:
        if selector:
            # Element-state wait. Default to "visible" if user didn't say.
            element_state = state or "visible"
            if element_state not in ("attached", "detached", "visible", "hidden"):
                return (
                    f"Error: with a selector, `state` must be attached / detached / "
                    f"visible / hidden (got {element_state!r})."
                )
            page.wait_for_selector(selector, state=element_state, timeout=timeout_ms)
            return f"Element `{selector}` reached state `{element_state}`."
        # Page-state wait.
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
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not code:
        return "Error: `code` is required for eval."
    try:
        result = sess["page"].evaluate(code)
        # Stringify and cap so a giant DOM dump can't flood the agent.
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


def _html(session_id: str, selector: str | None) -> str:
    sess = _require_session(session_id)
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
        # Cap at 5KB — enough for the model to skim structure / find ids.
        if len(html) > 5000:
            html = html[:5000] + f"\n\n[truncated, {len(html) - 5000} more chars]"
        return html
    except Exception as e:
        return f"Error fetching HTML: {type(e).__name__}: {e}"


def _hover(session_id: str, selector: str) -> str:
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not selector:
        return "Error: `selector` is required for select."
    if not value:
        return "Error: `value` is required for select."
    try:
        # Accept comma-list for multi-select.
        values = [v.strip() for v in value.split(",")] if "," in value else value
        sess["page"].select_option(selector, values)
        return f"Selected `{value}` in `{selector}`."
    except Exception as e:
        return f"Error selecting: {type(e).__name__}: {e}"


def _press(session_id: str, key: str) -> str:
    sess = _require_session(session_id)
    if isinstance(sess, str):
        return sess
    if not key:
        return "Error: `key` is required for press (e.g. 'Enter', 'Escape', 'Control+A')."
    try:
        sess["page"].keyboard.press(key)
        return f"Pressed `{key}`."
    except Exception as e:
        return f"Error pressing key: {type(e).__name__}: {e}"


def _accessibility(session_id: str, selector: str | None) -> str:
    """Return a YAML-style aria snapshot of the page or a subtree.

    Uses Playwright's locator.aria_snapshot(), which renders the
    accessibility tree as a compact role/name outline. Useful as an
    alternative to raw HTML when an LLM needs to find interactive
    elements without parsing CSS selectors out of class soup.
    """
    sess = _require_session(session_id)
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


def _screenshot_b64(session_id: str, selector: str | None = None) -> str:
    sess = _require_session(session_id)
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


def _tabs(session_id: str) -> str:
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
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
    sess = _require_session(session_id)
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


def _cookies(session_id: str) -> str:
    sess = _require_session(session_id)
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


def _close(session_id: str) -> str:
    sess = _sessions.pop(session_id, None)
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
    if not _sessions:
        return "(no open browser sessions)"
    lines = [f"Open browser sessions: {len(_sessions)}"]
    for sid, sess in _sessions.items():
        try:
            url = sess["page"].url
        except Exception:
            url = "?"
        lines.append(f"  {sid}  {url}")
    return "\n".join(lines)


def execute(
    action: str | None = None,
    session_id: str | None = None,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    submit: bool = False,
    path: str | None = None,
    code: str | None = None,
    state: str | None = None,
    key: str | None = None,
    value: str | None = None,
    tab_index: int | None = None,
    headless: bool = True,
    stealth: bool = True,
    engine: str = "auto",
    storage_state: str | None = None,
    name: str | None = None,
    timeout_ms: int = 30_000,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op")
    if not action:
        return "Error: `action` is required."
    action = action.lower()

    session_id = session_id or read_string_param(kw, "session_id", "sid")
    url = url or read_string_param(kw, "url")
    selector = selector or read_string_param(kw, "selector", "sel")
    text = text if text is not None else read_string_param(kw, "text", "value")
    path = path or read_string_param(kw, "path", "file")
    code = code if code is not None else read_string_param(kw, "code", "js", "expression")
    state = state or read_string_param(kw, "state")
    key = key or read_string_param(kw, "key", "shortcut")
    value = value if value is not None else read_string_param(kw, "value", "option")
    if tab_index is None:
        tab_raw = read_string_param(kw, "tab_index", "index", "tab")
        if tab_raw is not None:
            try:
                tab_index = int(tab_raw)
            except (TypeError, ValueError):
                tab_index = None

    if action == "open":
        eng = engine or read_string_param(kw, "engine", "backend") or "auto"
        ss = storage_state or read_string_param(kw, "storage_state", "state")
        cdp = read_string_param(kw, "cdp_url", "cdp")
        return _open(
            headless=headless,
            timeout_ms=timeout_ms,
            stealth=stealth,
            engine=eng,
            url=url,
            storage_state=ss,
            cdp_url=cdp,
        )
    if action == "save_login":
        nm = name or read_string_param(kw, "name", "host", "label")
        return _save_login(session_id or "", nm)
    if action == "navigate":
        return _navigate(session_id or "", url or "")
    if action == "click":
        return _click(session_id or "", selector or "")
    if action == "type":
        return _type(session_id or "", selector or "", text or "", submit=submit)
    if action == "extract":
        return _extract(session_id or "", selector or None)
    if action == "screenshot":
        return _screenshot(session_id or "", path or "")
    if action == "upload":
        return _upload(session_id or "", selector or "", path or "")
    if action == "wait":
        return _wait(session_id or "", selector or None, state, timeout_ms)
    if action == "eval":
        return _eval_js(session_id or "", code or "")
    if action == "html":
        return _html(session_id or "", selector or None)
    if action == "hover":
        return _hover(session_id or "", selector or "")
    if action == "select":
        return _select_option(session_id or "", selector or "", value or "")
    if action == "press":
        return _press(session_id or "", key or "")
    if action == "accessibility":
        return _accessibility(session_id or "", selector or None)
    if action == "screenshot_b64":
        return _screenshot_b64(session_id or "", selector or None)
    if action == "tabs":
        return _tabs(session_id or "")
    if action == "new_tab":
        return _new_tab(session_id or "")
    if action == "switch_tab":
        return _switch_tab(session_id or "", tab_index or 0)
    if action == "download":
        return _download(session_id or "", selector or "", path or "", timeout_ms)
    if action == "cookies":
        return _cookies(session_id or "")
    if action == "close":
        return _close(session_id or "")
    if action == "list":
        return _list()
    return f"Error: unknown action {action!r}."


__all__ = ["NAME", "SPEC", "DESCRIPTION", "execute", "check_playwright"]
