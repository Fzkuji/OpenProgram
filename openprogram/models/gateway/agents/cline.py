"""Cline (``$CLINE_DIR``, default ``~/.cline``; its data in ``$CLINE_DATA_DIR``, default ``<dir>/data``).

After yetone/magpie ``internal/agent/cline.go`` (MIT License, Copyright (c)
yetone). Cline's CLI (3.x) keeps its providers in
``<data>/settings/providers.json``::

    {"version": 1, "lastUsedProvider": "<id>", "modes": {}, "providers": {"<id>": {
      "settings": {"provider": "<id>", "apiKey", "model", "baseUrl",
        "headers": {...}, "reasoning": {"effort": ...}},
      "updatedAt": ..., "tokenSource": "manual"}}}

A session runs the model of ``lastUsedProvider``. A provider of one's own,
added to models.json, is refused when a session starts ("Unknown or disabled
provider", cline/cline#14180), so OpenProgram takes Cline's built-in
``openai-compatible`` provider and makes it the one in use. That provider
lists only gpt-4o, so the catalog goes in ``settings/models.json`` beside it,
as the entry Cline's own migration writes for it (``{"provider": {"name",
"baseUrl", "defaultModelId"}, "models": {...}}``); what the user set there
(a model's overrides from the VS Code extension's model editor, the
provider's protocol or capabilities) stays. What was there, and the provider
in use, are stashed and put back on disconnect. Cline's requests name only
the AI SDK, so the header says they are Cline's.

``reasoning`` is ``{"enabled": false}`` for no thinking, as Cline's own
pickers write it; an effort beside a false ``enabled`` is dropped, so an
effort is written ``{"enabled": true, "effort": ...}``.

Cline's VS Code extension (4.x) runs on the same files, but takes the
provider, model, base URL and key from its own state beside them first —
``globalState.json``'s ``act/planModeApiProvider`` (``openai`` for
openai-compatible), ``act/planModeOpenAiModelId`` and ``openAiBaseUrl``,
``secrets.json``'s ``openAiApiKey``. Where that state is, those are set too,
and stashed. The extension reads the files once, at start-up.

Cline's desktop app (0.0.x) keeps the provider, model and effort picked in
its composer in its web view's localStorage and never reads them from these
files: OpenProgram's models are in its picker, but which one it runs is
picked there.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

#: The provider OpenProgram takes in Cline.
SLOT = "openai-compatible"
HEADERS = {"User-Agent": "cline"}


def home() -> Path:
    return Path(os.environ.get("CLINE_DIR") or str(Path.home() / ".cline")).expanduser()


def data_dir() -> Path:
    data = os.environ.get("CLINE_DATA_DIR")
    return Path(data).expanduser() if data else home() / "data"


def config_path() -> Path:
    return data_dir() / "settings" / "providers.json"


def models_path() -> Path:
    return data_dir() / "settings" / "models.json"


def reasoning(effort: str) -> dict[str, Any]:
    """A provider's reasoning asking for ``effort``, ``none`` for no thinking."""
    return {"enabled": False} if effort == "none" else {"enabled": True, "effort": effort}


def effort_of(value: Any) -> str:
    """The effort a provider's reasoning asks for: none when it is off."""
    if not isinstance(value, dict):
        return ""
    if value.get("enabled") is False:
        return "none"
    return str(value.get("effort") or "")


def models_entry(model: str, models: list[dict[str, Any]], v1: str) -> dict[str, Any]:
    """OpenProgram's entry in models.json: every model, ``model`` the default."""
    listed: dict[str, Any] = {}
    for row in models:
        caps = ["streaming", "tools"]
        if row.get("vision"):
            caps.append("images")
        if row.get("thinking_levels"):
            caps.append("reasoning")
        entry: dict[str, Any] = {"id": row["id"], "name": row.get("name") or row["id"], "capabilities": caps}
        window, output = int(row.get("context_window") or 0), int(row.get("max_tokens") or 0)
        if window:
            entry["contextWindow"] = window
        if output:
            entry["maxTokens"] = min(output, window) if window else output
        listed[row["id"]] = entry
    return {"provider": {"name": "OpenProgram", "baseUrl": v1, "defaultModelId": model}, "models": listed}


def merge_kept(have: Any, want: dict[str, Any]) -> dict[str, Any]:
    """``want`` with each key of ``have`` it lacks, objects merged alike (magpie ``mergeKept``)."""
    if not isinstance(have, dict):
        return want
    out = dict(want)
    for key, value in have.items():
        if key not in want:
            out[key] = value
        elif isinstance(value, dict) and isinstance(want[key], dict):
            out[key] = merge_kept(value, want[key])
    return out


def models_kept(have: Any, want: dict[str, Any]) -> dict[str, Any]:
    """``want`` laid over the entry in models.json (magpie ``clineKept``, #2030):
    what the user set on the provider and on each listed model stays; a model
    no longer listed goes, with what was set on it."""
    if not isinstance(have, dict):
        return want
    had = have.get("models") if isinstance(have.get("models"), dict) else {}
    listed = {key: merge_kept(had.get(key), value) for key, value in want["models"].items()}
    out = merge_kept({k: v for k, v in have.items() if k != "models"}, {k: v for k, v in want.items() if k != "models"})
    out["models"] = listed
    return out


def _read(path: Path) -> dict[str, Any]:
    from .. import edit
    from ..edit import ConfigText
    text = ConfigText.read(path)
    return edit.load("json", text.text, str(path)) if text.exists else {}


def _wire(at: Wiring) -> list[Write]:
    from ..edit import get_path
    v1, path, models = at.base("openai-chat"), config_path(), models_path()
    providers = _read(path)
    have = get_path(providers, f"providers.{SLOT}")[1]
    in_use = providers.get("lastUsedProvider") or "cline"
    settings: dict[str, Any] = {"provider": SLOT, "apiKey": at.key, "model": at.model, "baseUrl": v1,
                                "headers": dict(HEADERS)}
    # the effort the provider in use asks for goes with the pick
    picked = effort_of(get_path(providers, f"providers.{in_use}.settings.reasoning")[1])
    if picked:
        settings["reasoning"] = reasoning(picked)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    if isinstance(have, dict) and have.get("settings") == settings and have.get("updatedAt"):
        stamp = have["updatedAt"]  # nothing changed: the same entry, not a new stamp
    sets: dict[str, Any] = {f"providers.{SLOT}": {"settings": settings, "updatedAt": stamp, "tokenSource": "manual"},
                            "lastUsedProvider": SLOT}
    if not path.exists():
        sets.update({"version": 1, "modes": {}})
    listed = models_kept(get_path(_read(models), f"providers.{SLOT}")[1], models_entry(at.model, at.models, v1))
    model_sets: dict[str, Any] = {f"providers.{SLOT}": listed}
    if not models.exists():
        model_sets["version"] = 1
    # both entries are OpenProgram's whole (Write.owns): they go on disconnect,
    # edited or not, and what was there before comes back. What the user sets
    # on a listed model is theirs (clineKept) and no drift; Cline stamps
    # updatedAt itself.
    writes = [Write(models, "json", sets=model_sets, owns=(f"providers.{SLOT}",),
                    loose=(f"providers.{SLOT}.models",), listing=True),
              Write(path, "json", sets=sets, owns=(f"providers.{SLOT}",), loose=(f"providers.{SLOT}.updatedAt",))]
    state, secrets = data_dir() / "globalState.json", data_dir() / "secrets.json"
    if state.exists():
        # the model info a pick leaves is the old model's; OpenProgram's is in models.json
        writes.append(Write(state, "json", sets={
            "actModeApiProvider": "openai", "planModeApiProvider": "openai",
            "actModeOpenAiModelId": at.model, "planModeOpenAiModelId": at.model,
            "openAiBaseUrl": v1, "openAiHeaders": dict(HEADERS)},
            deletes=["actModeOpenAiModelInfo", "planModeOpenAiModelInfo"]))
        writes.append(Write(secrets, "json", sets={"openAiApiKey": at.key}))
    return writes


def _notice() -> str:
    from .running import any_running
    if sys.platform == "win32":
        # running can't tell the desktop app from the CLI there
        return ("Cline reads its provider as a session starts — open sessions keep the model they have; new ones "
                "use this. Reload VS Code's window for its extension; Cline's desktop app keeps the model picked "
                "in its own composer — pick OpenProgram's there.")
    if any_running(r"Cline\.app/", r"(^|/)cline-app( |$)"):
        return ("Cline's desktop app keeps the model and effort picked in its own composer — pick OpenProgram's "
                "there; this sets Cline's CLI and VS Code extension.")
    if any_running(r"(^|/)cline( |$)"):
        return ("Cline reads its provider as a session starts — open sessions keep the model they have; new ones "
                "use this. Reload VS Code's window for its extension.")
    return ""


def agent() -> Agent:
    return Agent(
        id="cline", name="Cline", icon="cline", aliases=("cline-cli",), kind="cli", protocol="openai-chat",
        bin="cline", dir=home, path=config_path,
        ua=("cline",), named_key=True, wire=_wire, notice=_notice,
    )
