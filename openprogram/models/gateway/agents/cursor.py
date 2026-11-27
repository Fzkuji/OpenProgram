"""Cursor CLI, ``cursor-agent`` (``~/.cursor/cli-config.json``): its own models only.

After yetone/magpie ``internal/agent/agents.go`` ``cursor`` (MIT License,
Copyright (c) yetone). Cursor CLI has no endpoint the gateway can stand in
for, so its row sets only its model: ``model.modelId``,
``model.displayModelId`` and ``model.displayName``, with
``hasChangedDefaultModel``. Going back to its own default puts back what was
there before.

The row is Cursor CLI, not the Cursor editor. The editor makes ``~/.cursor``
(argv.json, extensions, mcp.json, projects) with no ``cursor-agent``
anywhere, so the folder says nothing of the CLI: ``cli-config.json`` or the
``cursor-agent`` command does.
"""
from __future__ import annotations

from pathlib import Path

from .base import Agent, Field, Wiring, Write, picked


def config_path() -> Path:
    return Path.home() / ".cursor" / "cli-config.json"


def _wire(at: Wiring) -> list[Write]:
    model = at.model
    return [Write(config_path(), "jsonc", sets={
        "model.modelId": model, "model.displayModelId": model, "model.displayName": model,
        "hasChangedDefaultModel": True,
    })]


def _options() -> list[tuple[str, str]]:
    current = picked("cursor").get("model", "")
    options = [("auto", "Auto")]
    if current and current != "auto":
        options.insert(0, (current, current))
    return options


def agent() -> Agent:
    return Agent(
        id="cursor", name="Cursor CLI", icon="cursor", aliases=("cursor-agent",), kind="cli",
        bin="cursor-agent", path=config_path, fields=[Field("model", "Model", options=_options)],
        ua=("cursor",), wire=_wire,
    )
