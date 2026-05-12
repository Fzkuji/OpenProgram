"""Browser ``open`` action + engine setup helpers."""
from __future__ import annotations

import uuid
from typing import Any


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
  // 4. languages — Playwright sets ['en-US'] by default
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

    Returns (pw_instance, browser_kind, name_or_err) where browser_kind
    is the launcher we'll call .launch() on, or (None, None, error_str)
    when the requested engine isn't installed.
    """
    from openprogram.tools.browser import browser as _b
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
            cam = Camoufox(headless=True)
            return cam, None, "camoufox"
        except ImportError:
            return None, None, (
                "Error: camoufox not installed. Run:\n"
                "  pip install \"openprogram[browser-stealth]\"\n"
                "  camoufox fetch"
            )
    try:
        from playwright.sync_api import sync_playwright as _sync_pw
        pw = _sync_pw().start()
        return pw, pw.chromium, "chromium"
    except ImportError:
        return None, None, _b._install_hint()


def _read_cdp_port() -> int | None:
    """If the user ran `openprogram browser attach` we wrote the port here."""
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
      - If `url` is given AND we have a saved login for that host, load
        the state and run headless — the agent gets a logged-in session
        with no manual step.
      - If `url` is given but we don't have a saved login, force
        headless=False so the user can log in manually, then prompt
        them to call ``save_login``.
      - If `storage_state` is given explicitly, that path overrides the
        host-based lookup.
    """
    from openprogram.tools.browser import browser as _b
    if not _b.check_playwright():
        return _b._install_hint()

    # Auto-bootstrap path (default): when the caller didn't pin a
    # specific engine and didn't pass cdp_url, ensure a sidecar Chrome
    # is running and route through CDP. First call may take a minute
    # because it copies the user's Chrome profile (~3GB); subsequent
    # calls are instant.
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
        if cdp_url is None:
            engine = "chromium"

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
            # Fresh page so we don't hijack whatever the user has open.
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            if url:
                try:
                    page.goto(url)
                except Exception:
                    pass
            session_id = "br_" + uuid.uuid4().hex[:10]
            _b._sessions[session_id] = {
                "engine": "cdp",
                "playwright": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "pages": [page],
                "active": 0,
                "default_timeout": timeout_ms,
                "login_url": url,
                "is_cdp": True,
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
        return name_or_err

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
    elif url and _b._has_saved_login(url):
        state_path = _b._state_path_for(url)
    elif url:
        # No saved login for this host — flip to headed so user can log in.
        if headless:
            headless = False
            auto_login_needed = True
    try:
        if name_or_err == "camoufox":
            # Camoufox manages its own context; everything below is redundant.
            cam = pw
            cm = cam.__enter__()
            page = cm.new_page()
            page.set_default_timeout(timeout_ms)
            session_id = "br_" + uuid.uuid4().hex[:10]
            _b._sessions[session_id] = {
                "engine": "camoufox",
                "playwright": cam,
                "browser": cm,
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
            # patchright already does deep patches; layering ours can
            # re-introduce detectable inconsistencies, so only apply to
            # stock chromium.
            context.add_init_script(f"({_STEALTH_INIT_SCRIPT})()")
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        if url:
            try:
                page.goto(url)
            except Exception:
                pass  # leave navigation issues for `navigate` to report
        session_id = "br_" + uuid.uuid4().hex[:10]
        _b._sessions[session_id] = {
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
