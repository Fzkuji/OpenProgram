"""VS Code Insiders's chat, set up as VS Code's is (``vscode.py``).

After yetone/magpie ``internal/agent/vscode.go`` (MIT License, Copyright (c)
yetone): Insiders keeps its own User folder (``Code - Insiders``) and its
chat names itself as Stable's does, so its models send a key of its own.
"""
from __future__ import annotations

from .base import Agent
from .vscode import INSIDERS, agent_of


def agent() -> Agent:
    return agent_of(INSIDERS)
