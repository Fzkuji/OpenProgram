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
    from openprogram.functions.tools.browser import browser as _b
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


# 应用 UI 本体的地址前缀（stable 18100 / dev 18200，见 desktop/main.js 的
# WEB_PORT）。attach 桌面应用时这些 target 永远跳过 —— agent 只能接管
# 可见的 web tab，绝不能拿到壳页面本身。
_SHELL_URL_PREFIXES = (
    "http://127.0.0.1:18100", "http://localhost:18100",
    "http://127.0.0.1:18200", "http://localhost:18200",
)


def _is_shell_page(page_url: str) -> bool:
    u = (page_url or "").lower()
    return u.startswith("devtools://") or u.startswith(_SHELL_URL_PREFIXES)


def _open_app_session(
    cdp_url: str,
    *,
    url: str | None,
    timeout_ms: int,
    strict: bool,
) -> str | None:
    """Attach to the visible web tabs inside the OpenProgram desktop app.

    可见性走控制面：url 给定时先经 WS 广播让桌面壳 openWebTab(url)
    （webui/ws_actions/webtab.py），再轮询 CDP targets 等新页面出现。
    直接 context.new_page() 在 Electron 上会弹出裸窗口而不是应用内
    tab，所以这里从不这么做。

    Returns the result string, or None when ``strict`` is False and the
    caller should fall back to the sidecar flow (shell unreachable over
    WS, no page appeared, ...).
    """
    import time as _time
    from openprogram.functions.tools.browser import browser as _b

    def _fail(msg: str) -> str | None:
        return f"Error: {msg}" if strict else None

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
    except ImportError:
        return _b._install_hint()
    try:
        from openprogram.functions.tools.browser._chrome_bootstrap import (
            desktop_app_ws_url,
        )
        # Electron 对 Playwright 的 http 握手路径回 400，必须用 ws URL。
        endpoint = desktop_app_ws_url() or cdp_url
        browser = pw.chromium.connect_over_cdp(endpoint)
    except Exception as e:
        pw.stop()
        return _fail(f"connecting to desktop app at {cdp_url}: {type(e).__name__}: {e}")

    # 壳页面与 web tab 可能落在不同 BrowserContext（default session vs
    # persist:webtabs），所以扫全部 contexts 而不是只看 contexts[0]。
    def _all_pages():
        return [p for ctx in browser.contexts for p in ctx.pages]

    def _visible_pages():
        return [p for p in _all_pages() if not _is_shell_page(p.url)]

    def _norm(u: str) -> str:
        return (u or "").rstrip("/")

    page = None
    if url:
        before = set(_all_pages())
        try:
            from openprogram.webui.ws_actions.webtab import request_open_tab
            reply = request_open_tab(url)
        except Exception as e:
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if reply.get("ok"):
            # 前端 openWebTab 后 WebContentsView 在 pane 挂载时才创建，
            # CDP target 晚于 WS 回执出现 —— 轮询最多 10s。已存在同 URL
            # tab 时前端只聚焦不新建，靠 URL 归一比对认领。
            deadline = _time.time() + 10.0
            while page is None and _time.time() < deadline:
                for p in _visible_pages():
                    if p not in before or _norm(p.url) == _norm(url):
                        page = p
                        break
                if page is None:
                    _time.sleep(0.25)
        if page is None:
            pw.stop()
            return _fail(
                "desktop app did not produce a visible tab for the URL ("
                + str(reply.get("error") or "no matching CDP target within 10s")
                + ")"
            )
    else:
        vis = _visible_pages()
        if not vis:
            pw.stop()
            return _fail(
                "no visible web tab open in the desktop app — pass `url` "
                "so one can be opened"
            )
        page = vis[-1]

    page.set_default_timeout(timeout_ms)
    session_id = "br_" + uuid.uuid4().hex[:10]
    _b._sessions[session_id] = {
        "engine": "app",
        "playwright": pw,
        "browser": browser,
        "context": page.context,
        "page": page,
        "pages": [page],
        "active": 0,
        "default_timeout": timeout_ms,
        "login_url": url,
        "is_cdp": True,
        "is_app": True,
    }
    return (
        f"Opened browser session `{session_id}` "
        f"(engine=app via {cdp_url}, attached to the visible web tab inside "
        f"the OpenProgram desktop app). Current page: {page.url}"
    )


def _read_cdp_port() -> int | None:
    """If the user ran `openprogram browser attach` we wrote the port here."""
    from pathlib import Path
    p = Path.home() / ".openprogram" / "browser-cdp-port"
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
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
    from openprogram.functions.tools.browser import browser as _b
    if not _b.check_playwright():
        return _b._install_hint()

    # Auto-bootstrap path (default): when the caller didn't pin a
    # specific engine and didn't pass cdp_url, ensure a sidecar Chrome
    # is running and route through CDP. First call may take a minute
    # because it copies the user's Chrome profile (~3GB); subsequent
    # calls are instant.
    auto_engine = engine in (None, "", "auto")
    app_engine = isinstance(engine, str) and engine.lower() == "app"

    # 桌面应用优先（对标 claude-in-chrome 的可见接管）：壳开着时 9223 上有
    # Electron 的 CDP，attach 它的可见 web tab；壳没开则 auto 原样落回
    # sidecar Chrome（9222），行为与从前完全一致。
    if cdp_url is None and (auto_engine or app_engine):
        from openprogram.functions.tools.browser._chrome_bootstrap import (
            desktop_app_cdp_url,
        )
        app_cdp = desktop_app_cdp_url()
        if app_cdp is not None:
            res = _open_app_session(
                app_cdp, url=url, timeout_ms=timeout_ms, strict=app_engine,
            )
            if res is not None:
                return res
            # auto：控制面无人应答 / 没等到页面 → 回落 sidecar。
        elif app_engine:
            return (
                "Error: engine='app' requires the OpenProgram desktop app "
                "running (CDP port 9223 unreachable) — launch the app, or "
                "use engine='auto'."
            )

    if cdp_url is None and auto_engine:
        from openprogram.functions.tools.browser._chrome_bootstrap import (
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
