"""Playwright attaches to the App only through ``attach_to_app``.

With Playwright's defaults every adopted page, the App's own window included,
is told the system is in light mode, and the App's theme flips while the
connection lasts.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from openprogram.programs.applications.browser.app_endpoint import attach_to_app

ROOT = Path(__file__).resolve().parents[3]


def test_only_attach_to_app_connects_over_cdp():
    callers = subprocess.run(
        ["git", "grep", "-lI", "--untracked", "-E", r"connect_over_cdp\(|connectOverCDP\(",
         "--", ":!references", ":!docs"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    assert callers == ["openprogram/programs/applications/browser/app_endpoint.py"]


def test_attach_to_app_turns_playwright_defaults_off():
    chromium = SimpleNamespace(connect_over_cdp=lambda endpoint, **options: (endpoint, options))
    assert attach_to_app(SimpleNamespace(chromium=chromium), "ws://app") == ("ws://app", {"no_defaults": True})
