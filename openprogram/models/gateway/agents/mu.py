"""mu (qybaihe/mu, npm's mu-agent, the ``mu`` command and the mu desktop app):
Pi with a judgment kernel added as an extension. Its kyrn/bin/mu.mjs starts
Pi named mu, so Pi's ``settings.json`` and ``models.json`` live in mu's own
agent folder, and the desktop app runs the same Pi on the same folder.

After yetone/magpie ``internal/agent/mu.go`` (MIT License, Copyright (c)
yetone); wired as Pi is (``pi_like.py``). mu's first-run check asks for
``mu setup`` only while models.json has no provider and settings.json no
defaultProvider, so this wiring is a setup it takes. Its requests say
``pi/<version>``, as Pi's do, so the key it is given names it. Its judge
(mu.json, MU_JUDGE) is mu's own and left alone; Gemini's API is left out as
in Pi's folder.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent
from .pi_like import env_dir, pi_like


def folder() -> Path:
    """``$MU_AGENT_DIR``, ``$KYRN_AGENT_DIR``, ``$MU_CODING_AGENT_DIR`` or
    ``$KYRN_CODING_AGENT_DIR``, else ``agent`` in ``~/.mu``, or in
    ``~/.kyrn``, its old name, while that is a folder and ``~/.mu`` isn't
    (mu.mjs ``agentDirFor``)."""
    found = env_dir("MU_AGENT_DIR", "KYRN_AGENT_DIR", "MU_CODING_AGENT_DIR", "KYRN_CODING_AGENT_DIR")
    if found is not None:
        return found
    home = Path.home()
    if not (home / ".mu").is_dir() and (home / ".kyrn").is_dir():
        return home / ".kyrn" / "agent"
    return home / ".mu" / "agent"


def not_mu_agent(found: str) -> bool:
    """A ``mu`` on PATH is another program's (maildir-utils' mu, a compiled
    program); mu-agent's is its mu.mjs, through a link or a shim naming it
    (magpie ``notMuAgent``)."""
    real = os.path.realpath(found)
    normal = real.replace("\\", "/")
    if normal.endswith("/mu.mjs") or "/mu-agent/" in normal:
        return False
    try:
        with open(real, "rb") as handle:
            head = handle.read(8 << 10)
    except OSError:
        return True
    return b"mu.mjs" not in head and b"mu-agent" not in head


def agent() -> Agent:
    return pi_like("mu", "mu", folder, named_key=True, drop_gemini=True, aliases=("mu-agent",),
                   bin_not=not_mu_agent)
