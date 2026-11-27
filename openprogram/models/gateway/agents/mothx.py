"""MothX (oschina/mothx, the ``mothx`` command; formerly vibecoding): a Go
terminal coding agent that reads its providers and the model it starts on
from one ``settings.json`` in its config folder: ``$MOTHX_DIR``, else
``%APPDATA%\\mothx`` on Windows and ``~/.mothx`` elsewhere.

After yetone/magpie ``internal/agent/mothx.go`` (MIT License, Copyright (c)
yetone), which follows zhenruyan's fork. Connecting writes::

    {"defaultProvider": "openprogram", "defaultModel": "<provider/model>",
     "providers": {"openprogram": {"baseUrl": "<gateway>/v1",
       "api": "openai-chat", "apiKey": "<MothX's key>", "thinkingFormat": "openai",
       "models": [{"id", "name", "reasoning", "input", "contextWindow", "maxTokens"}]}}}

``thinkingFormat: openai`` has MothX ask every model for a level as
``reasoning_effort``, which the gateway carries to each vendor's own form;
without it MothX guesses the vendor from the model id. A
``thinkingFormat`` already in the block may be the user's and stays, as does
anything else they put there (headers, httpProxy). Its requests say
``Vibecoding Client/<version>``, its old name an alias here, and its key
names it too. It reads settings.json at start-up and its own /model and
/auth dialogs write the whole file back, so the file is copied whole first.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .base import Agent, Field, Wiring, Write
from .pi_like import PICK, PROVIDER_ID, level_options, max_tokens, read_doc, theirs_kept


def folder() -> Path:
    value = os.environ.get("MOTHX_DIR", "")
    if value:
        return Path(value)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "mothx" if appdata else Path.home() / "AppData" / "Roaming" / "mothx"
    return Path.home() / ".mothx"


def config_path() -> Path:
    return folder() / "settings.json"


def model_entry(row: dict[str, Any]) -> dict[str, Any]:
    """One enabled model as MothX's own presets write one (magpie ``mothxModelJSON``)."""
    levels = [level for level in row.get("thinking_levels") or [] if level not in ("off", "none")]
    # reasoning is what has MothX send a thinking level at all
    entry: dict[str, Any] = {"id": row["id"], "name": row.get("name") or row["id"],
                             "reasoning": bool(levels) or bool(row.get("reasoning")),
                             "input": ["text", "image"] if row.get("vision") else ["text"]}
    if int(row.get("context_window") or 0) > 0:
        entry["contextWindow"] = int(row["context_window"])
    if max_tokens(row) > 0:
        entry["maxTokens"] = max_tokens(row)
    return entry


def _wire(at: Wiring) -> list[Write]:
    key = f"providers.{PROVIDER_ID}"
    doc = read_doc(config_path())
    block: dict[str, Any] = {"baseUrl": at.base("openai-chat"), "api": "openai-chat", "apiKey": at.key,
                             "thinkingFormat": "openai", "models": [model_entry(row) for row in at.models]}
    current = doc.get("providers", {}).get(PROVIDER_ID) if isinstance(doc.get("providers"), dict) else None
    if isinstance(current, dict) and "thinkingFormat" in current:
        del block["thinkingFormat"]
    block = theirs_kept(doc, key, block)
    picked: dict[str, Any] = {"defaultProvider": PROVIDER_ID, "defaultModel": at.model}
    if at.values.get("effort"):
        # what a session starts at; MothX sends none for a model that doesn't reason
        picked["defaultThinkingLevel"] = at.values["effort"]
    # a sync writes the provider alone (magpie's writes providers.magpie): the
    # model picked since in MothX's /model stays. The provider is
    # OpenProgram's whole, however /auth rewrote it; the pick goes back as
    # one while it is still OpenProgram's
    return [Write(config_path(), "json", sets={key: block}, owns=(key,), listing=True),
            Write(config_path(), "json", sets=picked, picks=(PICK,))]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)(mothx|vibecoding)( |$)"):
        return "MothX reads its settings at start-up — restart open mothx sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="mothx", name="MothX", icon="mothx", aliases=("vibecoding",), kind="cli",
        protocol="openai-chat", bin="mothx", dir=folder, path=config_path,
        fields=[Field("model", "Model"), Field("effort", "Thinking", quiet=True, options=level_options)],
        named_key=True, before_copy=True,
        wire=_wire, notice=_notice,
    )
