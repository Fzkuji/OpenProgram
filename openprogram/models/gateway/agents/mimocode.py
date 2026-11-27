"""MiMo Code, the CLI, and the engine inside Xiaomi MiMo, the desktop app.

After yetone/magpie ``internal/agent/agents.go`` ``mimocode`` (MIT License,
Copyright (c) yetone). Both read the same config in OpenCode's shape
(``opencode_format.py``), in ``$XDG_CONFIG_HOME/mimocode``, which
``$MIMOCODE_HOME`` moves to its own ``config`` folder. A fork keeps OpenCode
1's words. It sends its own User-Agent (``mimocode/<v>``) and gets the
gateway's plain key.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Field, Wiring, Write
from .opencode_format import config_file, config_home, opencode_writes

AGENT_ID = "mimocode"


def home() -> Path:
    moved = os.environ.get("MIMOCODE_HOME", "")
    if moved and os.path.isabs(moved):
        return Path(moved) / "config"
    return config_home() / "mimocode"


def config_path() -> Path:
    return config_file(home(), AGENT_ID)


def _wire(at: Wiring) -> list[Write]:
    return opencode_writes(config_path(), at, two=False)


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)mimo( |$)"):
        return "MiMo Code reads its config at start-up — restart open mimo sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="MiMo Code", icon="mimocode", aliases=("mimo",), kind="cli",
        protocol="openai-chat", bin="mimo", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("small", "Small model", quiet=True)],
        ua=("mimocode",), wire=_wire, notice=_notice,
    )
