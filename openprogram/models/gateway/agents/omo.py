"""OmO (omo-ai, oh-my-openagent): a fork of Pi, its engine senpi, with Pi's
``settings.json`` and ``models.json`` in ``~/.omo/agent``, or
``$OMO_CODING_AGENT_DIR``, else ``$SENPI_CODING_AGENT_DIR`` (its
bin/lib/agent-dir.js ``canonicalAgentDir``).

After yetone/magpie ``internal/agent/omo.go`` (MIT License, Copyright (c)
yetone); wired as Pi is (``pi_like.py``). Its requests say
``omo/<version>``. OmO reads ``PI_CODING_AGENT_DIR`` as well, after its own
two; that one is Pi's, and a folder both share is Pi's row's to edit.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent
from .pi_like import pi_like


def folder() -> Path:
    for name in ("OMO_CODING_AGENT_DIR", "SENPI_CODING_AGENT_DIR"):
        value = os.environ.get(name, "").strip()
        if value and os.path.isabs(value):
            return Path(os.path.normpath(value))
    return Path.home() / ".omo" / "agent"


def agent() -> Agent:
    return pi_like("omo", "OmO", folder, aliases=("oh-my-openagent", "omo-ai"))
