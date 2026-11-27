"""DevEco Code, Huawei's fork of OpenCode for HarmonyOS apps (openharmony-sig/deveco-code).

After yetone/magpie ``internal/agent/agents.go`` ``deveco``/``devecoDir``
(MIT License, Copyright (c) yetone). It keeps OpenCode's config shape
(``opencode_format.py``, OpenCode 1's words) in its own folder,
``$XDG_CONFIG_HOME/deveco``, merging ``config.json``, ``deveco.json`` and
``deveco.jsonc`` there (packages/opencode/src/config, loadGlobal);
``$DEVECO_CONFIG_DIR`` adds a folder read after it, so what is written there
wins. 0.2.3's requests to a provider of the config's say ``deveco/0.2.3
ai-sdk/…``, but much of the OpenCode code it keeps still sends OpenCode's
own User-Agent, so its key names it as well (magpie ``openCodeKeyAt``).
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Field, Wiring, Write
from .opencode_format import config_file, config_home, opencode_writes

AGENT_ID = "deveco"


def home() -> Path:
    """``$DEVECO_CONFIG_DIR`` when set to an absolute folder, else ``$XDG_CONFIG_HOME/deveco``."""
    moved = os.environ.get("DEVECO_CONFIG_DIR", "").strip()
    if moved and os.path.isabs(moved):
        return Path(moved)
    return config_home() / "deveco"


def config_path() -> Path:
    return config_file(home(), AGENT_ID)


def _wire(at: Wiring) -> list[Write]:
    return opencode_writes(config_path(), at, two=False)


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="DevEco Code", icon="deveco-color", kind="cli",
        protocol="openai-chat", bin="deveco", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("small", "Small model", quiet=True)],
        ua=("deveco",), named_key=True, wire=_wire,
    )
