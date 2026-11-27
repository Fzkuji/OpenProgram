"""Raycast AI's Custom Providers (``~/.config/raycast/ai/providers.yaml``).

After yetone/magpie ``internal/agent/raycast.go`` (MIT License, Copyright (c)
yetone). Raycast AI takes models of one's own as Custom Providers (Raycast
Pro, on Mac and Windows), from ``~/.config/raycast/ai/providers.yaml`` in the
user's folder on both, whatever ``XDG_CONFIG_HOME`` says: the manual
(manual.raycast.com/ai/custom-providers) names that path::

    providers:
      - id: openprogram
        name: OpenProgram
        base_url: http://127.0.0.1:18100/gateway/openai/v1   # without /chat/completions
        api_keys:
          openprogram: <Raycast's own key>
        models:
          - id: <provider>/<model>
            name: …
            context: …
            abilities:
              temperature: {supported: true}
              vision: {supported: …}
              system_message: {supported: true}
              tools: {supported: true}
              reasoning_effort: {supported: …}

Raycast watches the file and picks a change up without a restart. The model
is picked in Raycast's own model picker (Quick AI, AI Chat, AI Commands), not
in a file, so what connecting sets is that OpenProgram's models are in that
picker: one entry of the providers list, id ``openprogram``; the model picked
here is only remembered. The user's other entries and the top-level tools
block stay, and so does a key OpenProgram doesn't write in its own entry
(include_user_email, additional_parameters, a model's description).
Raycast's User-Agent isn't known, so its requests are told apart by their
key.

OpenProgram's entry is a member of the ``providers`` list beside the user's
entries: disconnecting takes out only it, the list going with it when it was
the only one, and the file when OpenProgram made it (magpie
``raycastUnwire``). Magpie's one field, ``provider``, turns the entry on and
off; here that is connecting and disconnecting the agent itself.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Agent, App, Field, Members, Wiring, Write, app_bundles
from .model_rows import PROVIDER_ID, PROVIDER_NAME, efforts, images, name, read_doc, window

#: Raycast's macOS bundle.
RAYCAST_APP = App(bundle_id="com.raycast.macos", names=("Raycast.app",))


def home() -> Path:
    return Path.home() / ".config" / "raycast"


def config_path() -> Path:
    return home() / "ai" / "providers.yaml"


def provider_entry(at: Wiring) -> dict[str, Any]:
    """OpenProgram's entry in providers.yaml (magpie ``raycastProviderYAML``).
    Every ability is said, as Raycast assumes tools and vision off and
    temperature on when one isn't: tools on, as every model the gateway
    serves an agent takes them and Raycast's AI Extensions need them; vision
    on a model that takes images; reasoning effort on a model with levels."""
    models = []
    for model in at.models:
        entry: dict[str, Any] = {"id": model["id"], "name": name(model) or model["id"]}
        if window(model):
            entry["context"] = window(model)
        entry["abilities"] = {
            "temperature": {"supported": True},
            "vision": {"supported": images(model)},
            "system_message": {"supported": True},
            "tools": {"supported": True},
            "reasoning_effort": {"supported": bool(efforts(model))},
        }
        models.append(entry)
    return {"id": PROVIDER_ID, "name": PROVIDER_NAME, "base_url": at.base("openai-chat"),
            "api_keys": {PROVIDER_ID: at.key}, "models": models}


def merge_kept(have: Any, want: Any) -> Any:
    """``want`` with each key of ``have`` that ``want`` lacks, mappings in both
    merged alike and the models list matched by id (magpie ``mergeYAMLKept``):
    a key OpenProgram doesn't write, at any depth, is the user's and stays,
    while a model no longer enabled leaves the list."""
    if not isinstance(have, dict) or not isinstance(want, dict):
        return want
    out = dict(want)
    for key, value in have.items():
        if key not in out:
            out[key] = value
        elif key == "models" and isinstance(value, list) and isinstance(out[key], list):
            by_id = {m.get("id"): m for m in value if isinstance(m, dict)}
            out[key] = [merge_kept(by_id[m["id"]], m) if isinstance(m, dict) and m.get("id") in by_id else m
                        for m in out[key]]
        elif isinstance(value, dict) and isinstance(out[key], dict):
            out[key] = merge_kept(value, out[key])
    return out


def _ours(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("id") == PROVIDER_ID


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    providers = read_doc("yaml", path).get("providers")
    have = next((p for p in providers if _ours(p)), None) if isinstance(providers, list) else None
    want = provider_entry(at)
    entry = merge_kept(have, want) if have is not None else want
    return [Write(path, "yaml", members={"providers": Members(match={"id": PROVIDER_ID}, items=[entry])},
                  listing=True)]


def _detect() -> bool:
    # the app on a Mac; its config folder elsewhere, which Raycast for
    # Windows makes at Reveal Providers Config. No command: Raycast has none
    return bool(app_bundles(RAYCAST_APP)) or home().is_dir()


def agent() -> Agent:
    return Agent(
        id="raycast", name="Raycast", icon="raycast", aliases=("raycast-ai",), kind="app",
        protocol="openai-chat", dir=home, path=config_path, fields=[Field("model", "Model")],
        wire=_wire, detect=_detect,
    )
