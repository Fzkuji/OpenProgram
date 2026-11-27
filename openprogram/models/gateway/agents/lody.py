"""Lody, a desktop app that runs Claude Code, Codex and other agents: an import link.

After yetone/magpie ``internal/agent/lody.go`` (MIT License, Copyright (c)
yetone). Lody takes a provider for this machine only through its own
confirmation dialog, opened by its import link::

    lody://provider/import?v=1&data=<base64url of the provider's JSON>

The link offers every runtime Lody has (Anthropic Messages for Claude Code,
Responses for Codex, Chat Completions for the rest). Lody accepts only
loopback HTTP and its public attribution token, never a LAN key or a vendor
credential, so the link is made only for a loopback gateway. There is no
public status API: whether Lody has the provider is never inferred from a
click, its runtime files or its private document store, so the row has no
"added" state.

On Linux the link is handed to the Lody installed here as its desktop entry
says (:func:`open_lody`): Lody's AppImage makes itself the default only for
its own channel's scheme and leaves lody:// to a desktop database nothing
builds, so xdg-open turns the link away though Lody is running.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from . import applink
from .base import Agent, Wiring, user_config_dir

SCHEME = "lody://provider/import?"
#: Entries Lody makes in its home folder (~/.lody, or ~/.lody-oss for its OSS
#: build) once it has run: a folder of that name another tool made isn't Lody.
MARKS = ("workspace-catalog.json", "loro-repo", "agent-binaries", "code-collab-v2")
#: The names Lody's builds go by: its app bundle on a Mac and its data folder
#: under the OS's config folder (Electron's userData).
NAMES = ("Lody", "Lody OSS", "Lody Nightly")
#: The desktop entries Lody's Linux builds go by, stable first.
ENTRIES = ("ai.lody.desktop.desktop", "ai.lody.desktop.nightly.desktop", "dev.loro.lody.oss.desktop")
_PLIST_ID = re.compile(rb"<key>CFBundleIdentifier</key>\s*<string>([^<]*)</string>")


def loopback_base(worker: str) -> str:
    """The Worker's address as Lody takes it, ``""`` for one it would refuse:
    plain http on a loopback host, no user, query, fragment or path."""
    try:
        url = urlparse(worker)
        port = url.port
    except ValueError:
        return ""
    if (url.scheme != "http" or url.username is not None or url.password is not None or url.query
            or url.fragment or url.path not in ("", "/") or worker.endswith("?")):
        return ""
    host = url.hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1") or url.netloc.endswith(":"):
        return ""
    if host == "::1":
        if not url.netloc.startswith("[::1]"):
            return ""
        host = "[::1]"
    if port is not None:
        if not 1 <= port <= 65535:
            return ""
        if port != 80:
            host += f":{port}"
    return "http://" + host


def link(at: Wiring) -> str:
    """The import link offering the gateway to every runtime Lody has."""
    root = loopback_base(at.worker)
    if not root:
        return ""
    anthropic = at.base("anthropic").replace(at.worker, root, 1)
    v1 = at.base("openai-chat").replace(at.worker, root, 1)
    models = v1 + "/models"
    payload = {
        "kind": "custom", "id": "openprogram", "name": "OpenProgram",
        "auth": {"method": "apiKey", "apiKey": at.key},
        "endpoints": [
            {"protocol": "anthropic-messages", "baseUrl": anthropic, "targets": ["claude-code"], "modelsUrl": models},
            {"protocol": "openai-responses", "baseUrl": v1, "targets": ["codex"], "modelsUrl": models},
            {"protocol": "openai-chat", "baseUrl": v1, "targets": ["pi", "dsh", "kimi-code", "grok", "bub"],
             "modelsUrl": models},
        ],
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return SCHEME + "v=1&data=" + base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def bundle(folders: list[Path]) -> bool:
    """An app bundle in ``folders`` is one of Lody's: ai.lody.desktop (or
    another ai.lody. build), or a plist that isn't XML, which says nothing
    either way. A Lody.app of another vendor's isn't."""
    for folder in folders:
        for name in NAMES:
            try:
                raw = (Path(folder) / f"{name}.app" / "Contents" / "Info.plist").read_bytes()
            except OSError:
                continue
            found = _PLIST_ID.search(raw)
            if found is None or found.group(1).decode("utf-8", "replace").strip().startswith("ai.lody."):
                return True
    return False


def detect(home: Optional[Path] = None) -> bool:
    home = home if home is not None else Path.home()
    for folder in (home / ".lody", home / ".lody-oss"):
        if any((folder / mark).exists() for mark in MARKS):
            return True
    config = user_config_dir()
    if any((config / name).is_dir() for name in NAMES):
        return True
    return sys.platform == "darwin" and bundle([Path("/Applications"), home / "Applications"])


def command(link_: str, platform: str = sys.platform) -> Optional[list[str]]:
    """The command Lody's desktop entry runs for the link, ``None`` with no
    entry whose program is there (and on macOS and Windows, where Lody's
    bundle and installer claim lody:// themselves)."""
    if platform in ("darwin", "win32"):
        return None
    dirs = applink.xdg_data_dirs()
    for name in ENTRIES:
        # the first folder with an entry of that name has the one that counts
        for folder in dirs:
            if not folder:
                continue
            try:
                text = (Path(folder) / "applications" / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            argv = applink.desktop_exec(text, link_)
            if argv:
                return argv
            break
    return None


def _start(argv: list[str]) -> None:
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def open_lody(link_: str, open_: Callable[[str], None] = applink.open_app_link,
              start: Callable[[list[str]], None] = _start, platform: str = sys.platform) -> None:
    """Hand the link to the Lody installed here (magpie ``OpenLody``): on
    Linux run its desktop entry with it (a second Lody passes the link to
    the running one and quits); else, or when that fails, the checked opener."""
    argv = command(link_, platform)
    if argv:
        try:
            start(argv)
            return
        except OSError:
            pass
    open_(link_)


def agent() -> Agent:
    return Agent(
        id="lody", name="Lody", icon="lody", kind="app", fields=[], ua=("lody",),
        path=lambda: Path.home() / ".lody", wire=lambda at: [],
        detect_only=True, detect=detect, link=link, open_link=open_lody,
    )
