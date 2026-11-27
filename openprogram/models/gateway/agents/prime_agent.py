"""Prime Agent (PrimeIntellect-ai/prime-agent, the ``prime-agent`` command):
a Rust agent that keeps Pi's ``settings.json`` and ``models.json`` in
``$PRIME_AGENT_CODING_AGENT_DIR`` (``~`` standing for home), else
``~/.prime/agent`` (pa-types platform/dirs.rs ``agent_dir``).

After yetone/magpie ``internal/agent/primeagent.go`` (MIT License, Copyright
(c) yetone); wired as Pi is (``pi_like.py``). Its requests carry no
User-Agent, so the key it is given names it. Only its global settings.json
is written: a project's ``.prime/agent/settings.json``, ``allowedModels``
and ``subagentDefaultModel`` are the user's.
"""
from __future__ import annotations

from pathlib import Path

from .base import Agent
from .pi_like import env_dir, pi_like


def folder() -> Path:
    return env_dir("PRIME_AGENT_CODING_AGENT_DIR") or Path.home() / ".prime" / "agent"


def agent() -> Agent:
    return pi_like("prime-agent", "Prime Agent", folder, named_key=True, icon="prime-agent",
                   aliases=("prime_agent", "primeagent"))
