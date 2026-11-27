"""Air in JetBrains IDEs: the Air plugin, bundled with 2026.3 IDEs and
installed from JetBrains Marketplace in 2026.2 (com.intellij.air).

After yetone/magpie ``internal/agent/air.go`` ``airIDE`` (MIT License,
Copyright (c) yetone). It isn't the standalone app's code: its
AcpConfigurationLoader reads the custom ACP agents from
``~/.jetbrains/acp.json`` on every system, the file AI Assistant's "Add
Custom Agent" writes too. OpenProgram adds the same OpenProgram agent there
as for the app (``air.py``), with its OpenCode config beside acp.json.

Its own agents run on JetBrains AI or the agent's own login; its Codex runs
this computer's ``codex app-server`` and its Claude Agent this computer's
``claude``, so both are on the gateway while Codex and Claude Code are
connected here, and the model picked on this row is the OpenProgram agent's
alone.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .air import ENTRY, OPENCODE_FILE, air_notice, air_wire
from .base import Agent, Field
from .opencode_format import config_home

#: An IDE's config folder name: the product, then its year and release.
VERSIONED = re.compile(r"^[A-Za-z]+(\d{4})\.(\d+)$")


def home() -> Path:
    return Path.home() / ".jetbrains"


def config_path() -> Path:
    return home() / "acp.json"


def ide_since(year: int, minor: int) -> bool:
    """Whether a JetBrains IDE of ``year.minor`` or later has kept its settings
    here: a config folder named for the IDE and its version under JetBrains'
    folder (magpie ``jetBrainsIDESince``)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "JetBrains"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")) / "JetBrains"
    else:
        base = config_home() / "JetBrains"
    try:
        entries = list(os.scandir(base))
    except OSError:
        return False
    for entry in entries:
        found = VERSIONED.match(entry.name)
        if entry.is_dir() and found:
            y, n = int(found.group(1)), int(found.group(2))
            if y > year or y == year and n >= minor:
                return True
    return False


def _detect() -> bool:
    """``~/.jetbrains`` is there for AI Assistant's agents too: an IDE of
    2026.2 or later, or OpenProgram's agent already in acp.json, says Air can be."""
    if ide_since(2026, 2):
        return True
    try:
        doc = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    server = ((doc.get("agent_servers") or {}).get(ENTRY.split(".", 1)[1]) or {}) if isinstance(doc, dict) else {}
    return isinstance(server, dict) and (server.get("env") or {}).get("OPENCODE_CONFIG") == str(home() / OPENCODE_FILE)


def agent() -> Agent:
    return Agent(
        id="air_ide", name="Air (JetBrains IDEs)", icon="air",
        aliases=("air-ide", "air-plugin", "air-in-ides", "jetbrains-acp"), kind="ide",
        protocol="openai-chat", dir=home, path=config_path, fields=[Field("model", "Model")],
        detect=_detect, detect_only=True, wire=air_wire(config_path()), named_key=True,
        notice=air_notice(config_path(), "In your JetBrains IDE (2026.3, or 2026.2 with the Air plugin), pick "
                                         "OpenProgram in Air's agent menu — restart the IDE if it isn't listed yet."),
    )
