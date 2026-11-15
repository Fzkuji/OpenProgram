"""The browser the runtime starts itself, with no window, when no App window can open a page.

A page an agent opens goes into the App when one of its windows is there: the
user sees it and it has the App's sign-ins. With the App closed, or in a
process the App cannot be reached from, the tab driver starts a browser of its
own (``programs/applications/browser/tabs.py``). This module only answers
whether one can be started here, and starts it: the Chromium that ships with
the runtime, else the user's installed Chrome or Edge as a program, with a
fresh profile and none of the user's data.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

# Playwright's own Chromium first: the full browser, then the default headless shell.
_LAUNCHES: tuple[dict[str, str], ...] = ({"channel": "chromium"}, {}, {"channel": "chrome"}, {"channel": "msedge"})
_found: bool | None = None


def _bundled_folders() -> list[Path]:
    """Where Playwright keeps the browsers it downloaded, or the runtime ships."""
    named = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if named and named != "0":
        return [Path(named)]
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Caches" / "ms-playwright"]
    if os.name == "nt":
        return [Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ms-playwright"]
    return [Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "ms-playwright"]


def _installed() -> bool:
    """The user's Chrome or Edge, used only as a program to run."""
    if sys.platform == "darwin":
        return any(Path(root, f"{name}.app").exists()
                   for root in ("/Applications", Path.home() / "Applications")
                   for name in ("Google Chrome", "Microsoft Edge"))
    if os.name == "nt":
        roots = [os.environ.get(key) for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return any(Path(root, *parts).exists() for root in roots if root
                   for parts in (("Google", "Chrome", "Application", "chrome.exe"),
                                 ("Microsoft", "Edge", "Application", "msedge.exe")))
    return any(shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "microsoft-edge"))


def available() -> bool:
    """Whether a browser can be started here. Found once per process; a miss is looked for again."""
    global _found
    if not _found:
        try:
            import importlib.util
            has_playwright = importlib.util.find_spec("playwright") is not None
        except (ImportError, ValueError):
            has_playwright = False
        _found = has_playwright and (
            any(folder.is_dir() and any(folder.glob("chromium*")) for folder in _bundled_folders()) or _installed())
    return bool(_found)


def launch(playwright: Any) -> Any:
    """Start the browser with no window; raises the last failure when none starts."""
    failure: Exception | None = None
    for options in _LAUNCHES:
        try:
            return playwright.chromium.launch(headless=True, **options)
        except Exception as exc:  # noqa: BLE001 - the next browser is tried
            failure = exc
    raise failure if failure is not None else RuntimeError("no browser to start")
