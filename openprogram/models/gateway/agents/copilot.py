"""GitHub Copilot CLI through its own providers (BYOK; ``$COPILOT_HOME``, default ``~/.copilot``).

After yetone/magpie ``internal/agent/agents.go`` ``copilot`` and
``copilot_byok.go`` (MIT License, Copyright (c) yetone). Copilot CLI takes
model providers of one's own from ``providers.json`` (``copilot help
providers``), ``$COPILOT_PROVIDERS_CONFIG`` else beside ``settings.json``::

    {"providers": [{"name", "type", "baseUrl", "apiKey", "wireApi"}],
     "models":    [{"id", "provider", "name", "maxPromptTokens", "maxOutputTokens", ...}]}

and a model is picked as ``<provider>/<id>`` (``settings.json`` ``model``).
It needs no GitHub sign-in. OpenProgram owns the entry named
``openprogram`` in ``providers`` and the entries of ``models`` that name it;
every other entry, and any other key of the file, is the user's and stays,
and so does a key the user added inside OpenProgram's own provider entry.
The wire is OpenAI's Chat Completions (``type: openai``), which the gateway
translates for every model; the key names Copilot CLI.

``COPILOT_HOME`` moves the whole folder. GitHub Copilot's JetBrains and VS
Code plugins write ``~/.copilot`` too (agents, skills, instructions), so the
folder says nothing of the CLI: its ``settings.json`` or the ``copilot``
command does. VS Code 1.143 ships a ``copilot`` launcher of its own in its
install folder, which only asks for the CLI to be installed: a command beside
an editor's ``product.json`` is not the CLI.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write
from .json_kept import merge_theirs, read_json

PROVIDER_ID = "openprogram"


def home() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or str(Path.home() / ".copilot")).expanduser()


def config_path() -> Path:
    return home() / "settings.json"


def providers_path() -> Path:
    """Copilot CLI's multi-provider file: ``$COPILOT_PROVIDERS_CONFIG``, else providers.json."""
    named = os.environ.get("COPILOT_PROVIDERS_CONFIG")
    return Path(named).expanduser() if named else home() / "providers.json"


def output_within(model: dict[str, Any]) -> int:
    """The model's output limit kept within its window (magpie ``maxTokens``)."""
    out, window = int(model.get("max_tokens") or 0), int(model.get("context_window") or 0)
    return min(out, window) if out and window else out


def block(models: list[dict[str, Any]], url: str, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """OpenProgram's provider and its models (magpie ``copilotBlock``)."""
    provider = {"name": PROVIDER_ID, "type": "openai", "baseUrl": url, "apiKey": key}
    listed = []
    for model in models:
        entry: dict[str, Any] = {"id": model["id"], "provider": PROVIDER_ID}
        if model.get("name"):
            entry["name"] = model["name"]
        out = output_within(model)
        if out > 0:
            entry["maxOutputTokens"] = out
        window = int(model.get("context_window") or 0)
        if window > 0:
            entry["maxContextWindowTokens"] = window
            entry["maxPromptTokens"] = window - out if 0 < out < window else window
        listed.append(entry)
    return provider, listed


def _wire(at: Wiring) -> list[Write]:
    path, url = providers_path(), at.base("openai-chat")
    provider, listed = block(at.models, url, at.key)
    providers = read_json(path).get("providers")
    for have in providers if isinstance(providers, list) else []:
        if isinstance(have, dict) and have.get("name") == PROVIDER_ID:
            provider = merge_theirs(have, provider)  # what the user added to it stays
            break
    return [
        Write(path, "json", listing=True, members={
            # each where OpenProgram's was, else at the end (magpie copilotWrite)
            "providers": Members({"name": PROVIDER_ID}, [provider], keep_place=True),
            "models": Members({"provider": PROVIDER_ID}, listed, keep_place=True),
        }),
        Write(config_path(), "json", sets={"model": f"{PROVIDER_ID}/{at.model}"}),
    ]


_COMMIT = re.compile(r"^[0-9a-f]{10,40}$")


def editor_launcher(binary: str) -> bool:
    """Whether ``binary`` is a launcher an editor ships beside itself, told by
    the ``product.json`` an editor's install keeps (magpie ``editorLauncher``)."""
    folder = Path(os.path.realpath(binary)).parent
    for _ in range(3):
        if (folder / "product.json").is_file() or (folder / "resources" / "app" / "product.json").is_file():
            return True
        try:
            for entry in folder.iterdir():
                if entry.is_dir() and _COMMIT.match(entry.name) and \
                        (entry / "resources" / "app" / "product.json").is_file():
                    return True
        except OSError:
            pass
        if folder.parent == folder:
            break
        folder = folder.parent
    return False


def agent() -> Agent:
    return Agent(
        id="copilot", name="Copilot CLI", icon="githubcopilot", aliases=("gh-copilot",), kind="cli",
        protocol="openai-chat", bin="copilot", dir=None, path=config_path,
        ua=("copilot", "github-copilot"), named_key=True, wire=_wire,
        # VS Code ships a copilot launcher of its own beside the editor (magpie binNot)
        bin_not=editor_launcher,
    )
