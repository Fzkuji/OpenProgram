"""VSCodium's chat, through the Marketplace's GitHub Copilot Chat (``vscode.py``).

After yetone/magpie ``internal/agent/vscode.go`` (MIT License, Copyright (c)
yetone): VSCodium has no Copilot Chat of its own; the group is written for
the provider the Copilot Chat installed in ``~/.vscode-oss/extensions``
declares (Custom Endpoint, or 0.48.1's OpenAI Compatible one).
"""
from __future__ import annotations

from .base import Agent
from .vscode import VSCODIUM, agent_of


def agent() -> Agent:
    return agent_of(VSCODIUM)
