"""The Antigravity plugin that starts the fast tools, after magpie's
``internal/fasttools/plugin.go`` (MIT License, Copyright (c) yetone).

magpie's plugin starts magpie's own program (``<exe> fast-tools mcp``); here
it starts OpenProgram, given as the argv that runs it (``[sys.executable,
"-m", "openprogram"]``), with ``fast-tools mcp`` and ``fast-tools
session-start`` after it as magpie's. A command already ending in
``fast-tools mcp`` is taken as that argv with them.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

from .tools import SERVER_NAME, TOOL_NAMES

# PLUGIN_NAME is the plugin's folder under Antigravity's plugins.
PLUGIN_NAME = "fast-tools"

# RULES_VERSION is the version of what write writes: a plugin written by an
# older OpenProgram is written again (keep). Module-level for the tests.
RULES_VERSION = 1

# MARK_FILE, in the plugin's folder, is how OpenProgram knows the plugin is
# its own: a folder of this name without it, someone's own plugin, isn't
# touched. It isn't in plugin.json, where Antigravity's strict check
# refuses a field it doesn't know.
MARK_FILE = ".openprogram.json"


class NotOursError(Exception):
    """A plugin of this name that OpenProgram didn't write is there."""

    def __init__(self) -> None:
        super().__init__(f"a plugin named {PLUGIN_NAME} that openprogram didn't write is there already")


def plugin_dir(home: str | os.PathLike) -> Path:
    """The plugin's folder: Antigravity's global customization folder,
    ~/.gemini/config, shared by its CLI and IDE, finds a plugin in
    plugins/<name> by its plugin.json."""
    return Path(home) / ".gemini" / "config" / "plugins" / PLUGIN_NAME


def written(home: str | os.PathLike) -> int:
    """The RULES_VERSION of the plugin OpenProgram wrote there; 0 for none,
    or a mark that can't be read."""
    try:
        b = (plugin_dir(home) / MARK_FILE).read_bytes()
        m = json.loads(b)
    except (OSError, ValueError):
        return 0
    if m is None:
        return 0
    if not isinstance(m, dict):
        return 0
    v = m.get("fastTools")
    if v is None:
        v = next((m[k] for k in m if isinstance(k, str) and k.lower() == "fasttools"), None)
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return 0
    return v


def _has_manifest(home: str | os.PathLike) -> bool:
    try:
        os.lstat(plugin_dir(home) / "plugin.json")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def is_on(home: str | os.PathLike) -> bool:
    """The plugin OpenProgram writes is there."""
    return written(home) > 0 and _has_manifest(home)


def _argv(command: Sequence[str]) -> list[str]:
    argv = [str(w) for w in command]
    if len(argv) >= 3 and argv[-2:] == ["fast-tools", "mcp"]:
        argv = argv[:-2]
    if not argv or argv[0] == "":
        raise ValueError("no openprogram program to start")
    return argv


def write(home: str | os.PathLike, command: list[str]) -> None:
    """Write the plugin, its MCP server and its SessionStart hook starting
    command, the argv that runs OpenProgram."""
    from .edits import write_file

    argv = _argv(command)
    # a plugin.json without OpenProgram's mark is someone's own
    if _has_manifest(home) and written(home) == 0:
        raise NotOursError()
    d = plugin_dir(home)
    tools: dict[str, Any] = {}
    for t in TOOL_NAMES:
        # eager: the tools are in every turn's list, not looked up first
        tools[t] = {"eager": True}
    # the mark first and plugin.json last: a folder left half-written is
    # still known as OpenProgram's, and isn't a plugin until it is whole
    files: list[tuple[str, Any]] = [
        (MARK_FILE, {"fastTools": RULES_VERSION}),
        ("mcp_config.json", {"mcpServers": {
            SERVER_NAME: {"command": argv[0], "args": argv[1:] + ["fast-tools", "mcp"], "tools": tools},
        }}),
        ("hooks.json", {PLUGIN_NAME: {
            "SessionStart": [{"type": "command", "command": hook_command(argv), "timeout": 5}],
        }}),
        ("rules/AGENTS.md", RULES),
        ("assets/logo.svg", LOGO),
        ("plugin.json", {
            "name": PLUGIN_NAME,
            "displayName": "Fast tools",
            "version": f"1.0.{RULES_VERSION}",
            "description": "Batched search, reads, edits and checks, and a workspace snapshot at the start of a conversation.",
            "logo": "assets/logo.svg",
        }),
    ]
    for name, v in files:
        text = v if isinstance(v, str) else json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        write_file(str(d / name), text, 0o644)


# LOGO is the plugin's icon in Antigravity: a bolt, grey to sit in light
# and dark.
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M9.25 2.25 4 9h3.75l-1 4.75L12 7H8.25z" fill="none" stroke="#888" stroke-width="1.3" stroke-linejoin="round"/></svg>
"""

_SH_SPECIAL = " \t'\"\\$`!&;|<>()*?[]#~{}"


def _goos() -> str:
    return "windows" if os.name == "nt" else sys.platform


def hook_command(command: Sequence[str]) -> str:
    """The hook's command line: command quoted for the shell Antigravity
    runs a hook in, sh -c on unix and cmd /c on Windows."""
    return hook_command_on(command, _goos())


def hook_command_on(command: Sequence[str], goos: str) -> str:
    argv = list(command)
    if goos == "windows":
        # cmd takes a "…" path as one word; a Windows path has no "
        words = ['"' + argv[0] + '"'] + [
            '"' + w + '"' if w == "" or " " in w or "\t" in w else w for w in argv[1:]
        ]
    else:
        words = []
        for w in argv:
            if w == "" or any(c in _SH_SPECIAL for c in w):
                w = "'" + w.replace("'", "'\\''") + "'"
            words.append(w)
    return " ".join(words) + " fast-tools session-start"


def remove(home: str | os.PathLike) -> None:
    """Take the plugin out, if it is OpenProgram's."""
    d = plugin_dir(home)
    try:
        os.stat(d)
    except FileNotFoundError:
        return
    except OSError:
        pass
    if written(home) == 0:
        raise NotOursError()
    if os.path.islink(d):
        os.unlink(d)
    elif os.path.exists(d):
        shutil.rmtree(d)


def keep(home: str | os.PathLike, command: list[str]) -> None:
    """Write the plugin again when it is on but starts a program that is no
    longer there (OpenProgram was moved, or reinstalled at another path),
    or was written by an older OpenProgram. One that starts another
    OpenProgram still there is left as it is: two of them (a build of one's
    own beside the app) would otherwise each take it back on every start.
    It never turns the plugin on."""
    v = written(home)
    if v == 0 or not command:
        return
    if v >= RULES_VERSION and _has_manifest(home) and _starts_there(home):
        return
    write(home, command)


def _starts_there(home: str | os.PathLike) -> bool:
    """The program the plugin's MCP server starts is there."""
    try:
        c = json.loads((plugin_dir(home) / "mcp_config.json").read_bytes())
    except (OSError, ValueError):
        return False
    if not isinstance(c, dict):
        return False
    servers = c.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    s = servers.get(SERVER_NAME)
    cmd = s.get("command") if isinstance(s, dict) else None
    if not isinstance(cmd, str) or cmd == "":
        return False
    try:
        return stat.S_ISREG(os.stat(cmd).st_mode)
    except OSError:
        return False


# RULES are the plugin's rules: when to reach for which tool. They go to
# the model with every turn, so they are short and say nothing but how to
# work.
RULES = """# Batched tools

The fast MCP server's tools each do in one call what otherwise takes many.
Use them when the work splits into independent parts; pass cwd, the
workspace's absolute path, every time.

- Several searches (text, regex, file globs, read-only git): one
  batch_search with all of them, not one search a turn.
- Several files, or parts of them: one read_slices. A long file comes as an
  outline first; then ask for the line ranges needed.
- A change across files: one apply_edits with every edit. Nothing is written
  unless every edit applies; give verify (a build or test command) to check
  the result in the same call.
- Independent builds, tests and linters: one run_checks; they run side by
  side and only failures show their output.

Keep using the built-in tools for one quick search, read or edit, and for
anything these don't do.
"""
