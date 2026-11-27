"""Pi (``pi``, the pi-coding-agent): ``settings.json`` and ``models.json`` in
its agent folder, ``$PI_CODING_AGENT_DIR`` (``~`` standing for home) else
``~/.pi/agent`` (Pi's config.js ``getAgentDir``).

After yetone/magpie ``internal/agent/agents.go`` ``pi``/``piIn``/``piDir`` and
``pi.go`` (MIT License, Copyright (c) yetone); the format is ``pi_like.py``.
A relative ``PI_CODING_AGENT_DIR`` is Pi's working directory's, which
OpenProgram can't know, so it is not taken. Gemini's API is left out of
Pi's folder (magpie ``piAsked``): pi-ai's Google adapter takes no
per-request fetch, so a Gemini model is asked on Chat. magpie's Pi
extension (``extensions/magpie.ts``, ``pi_extension.go``) registers
magpie's decision and image models for Pi's codemode, on magpie's
``/v1/systemone`` and ``/v1/images/generations``; OpenProgram's gateway
serves neither, so there is nothing for such a file to list and none is
written. magpie's pi-magpie package is magpie's own program.
"""
from __future__ import annotations

from pathlib import Path

from .base import Agent
from .pi_like import env_dir, pi_like


def folder() -> Path:
    return env_dir("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent"


def agent() -> Agent:
    return pi_like("pi", "Pi", folder, drop_gemini=True, protocol=True, ua=("pi-",))
