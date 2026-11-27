"""Open Code Review (alibaba/open-code-review, ``ocr``; ``~/.opencodereview/config.json``).

After yetone/magpie ``internal/agent/ocr.go`` (MIT License, Copyright (c)
yetone). ``ocr`` is a Go CLI that reviews a diff with a model; its plugins
for Claude Code, Codex, Cursor, Kimi Code and OpenCode, and its skills, all
run that same command. Its settings (``os.UserHomeDir``, no variable moves
them): ``provider`` names the entry in use, from ``providers`` (its presets)
or ``custom_providers`` (any other name), and ``model`` the model, the
entry's own ``model`` winning (internal/llm/resolver.go). A custom entry
needs a protocol, a url and a key of its own: no variable stands in for it.

Connecting adds the custom provider ``openprogram``: the ``openai``
protocol, which posts Chat Completions to ``url + /chat/completions``, the
gateway's ``/v1``, a key naming Open Code Review, and the model picked,
which goes to the gateway as it is. ``provider`` points at it; what the user
had is stashed and put back on disconnect. Requests carry
``open-code-review/<version>``. ``ocr`` reads the file on every run, so a
change needs no restart.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Wiring, Write

AGENT_ID = "open-code-review"
PROVIDER_ID = "openprogram"
ENTRY = f"custom_providers.{PROVIDER_ID}"


def home() -> Path:
    return Path.home() / ".opencodereview"


def config_path() -> Path:
    return home() / "config.json"


def ours(command: str) -> bool:
    """An ``ocr`` command is Open Code Review's only under a folder named for
    it (npm's), the command or where its link leads (magpie ``ocrNotOurs``);
    ``ocr`` is other programs' name too."""
    for candidate in (command, os.path.realpath(command)):
        lowered = candidate.lower().replace("\\", "/")
        if "open-code-review" in lowered or "opencodereview" in lowered:
            return True
    return False


def _wire(at: Wiring) -> list[Write]:
    return [Write(config_path(), "json", sets={
        f"{ENTRY}.protocol": "openai", f"{ENTRY}.url": at.base("openai-chat"), f"{ENTRY}.api_key": at.key,
        f"{ENTRY}.model": at.model, "provider": PROVIDER_ID})]


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="Open Code Review", icon=AGENT_ID, aliases=("ocr",), kind="cli", protocol="openai-chat",
        bin="ocr", dir=home, path=config_path, ua=(AGENT_ID,), named_key=True, wire=_wire,
        bin_not=lambda found: not ours(found),
    )
