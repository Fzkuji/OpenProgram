"""OpenCode (``opencode.json`` in ``$OPENCODE_CONFIG_DIR``, else ``$XDG_CONFIG_HOME/opencode``).

After yetone/magpie ``internal/agent/agents.go`` ``opencode``/``openCodeLike``/
``openCodeDir`` and ``opencode_v2.go`` (MIT License, Copyright (c) yetone).
Connecting writes OpenProgram's provider (``opencode_format.py``), ``model``
and the small model when one is picked, in OpenCode 2's words for an
OpenCode 2 on PATH; an OpenCode that loads plugins (1.4.0, 2.0.5 on) gets the
plugin that fetches the model list from the gateway as it starts.
OpenCode is told apart at the gateway by its User-Agent (``opencode/<v>``),
so it is given the gateway's plain key. OpenChamber runs on this same
config (``openchamber.py``).
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Field, Wiring, Write
from .opencode_format import config_file, config_home, discovers, installed_version, is_two, opencode_writes

AGENT_ID = "opencode"


def home() -> Path:
    """The folder OpenCode reads its user config from: ``$OPENCODE_CONFIG_DIR``
    when set, else ``$XDG_CONFIG_HOME/opencode``. OpenCode 2 reads that folder
    in place of the other; OpenCode 1 reads both, the variable's last, so
    what is written there wins in either (magpie ``openCodeDir``)."""
    moved = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
    if moved:
        return Path(os.path.abspath(os.path.expanduser(moved)))
    return config_home() / "opencode"


def config_path() -> Path:
    return config_file(home(), AGENT_ID)


def version() -> str:
    """The version of the opencode on PATH (patched in tests)."""
    return installed_version("opencode")


def _wire(at: Wiring) -> list[Write]:
    found = version()
    return opencode_writes(config_path(), at, two=is_two(found), discover=discovers(found))


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)opencode( |$)"):
        return "OpenCode reads its config at start-up — restart open opencode sessions to use this."
    return ""


def _held() -> list[Path]:
    return [Path.home() / ".opencode" / "bin" / "opencode"]


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="OpenCode", icon="opencode", aliases=("oc",), kind="cli",
        protocol="openai-chat", bin="opencode", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("small", "Small model", quiet=True)],
        ua=("opencode",), wire=_wire, notice=_notice, held_at=_held,
    )
