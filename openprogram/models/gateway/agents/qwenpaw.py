"""QwenPaw (agentscope-ai/QwenPaw, formerly CoPaw), one row per agent of its own.

After yetone/magpie ``internal/agent/qwenpaw.go`` (MIT License, Copyright (c)
yetone). QwenPaw is a personal assistant app with agents of its own.
Everything is read from its working folder: ``$QWENPAW_WORKING_DIR``, else
``~/.copaw`` when a legacy one is there, else ``~/.qwenpaw`` (resolved, so a
link is followed). Its secret folder is ``$QWENPAW_SECRET_DIR``, which
``<working folder>/.env`` may set too, else ``<working folder>.secret``.

``<working folder>/config.json`` lists the agents (``agents.profiles`` in
``agents.agent_order``); ``default`` is always there, in
``workspaces/default``. Each agent's ``<workspace_dir>/agent.json`` names its
model, ``"active_model": {"provider_id", "model"}``, beside
``fallback_models`` and ``subagent_model``, which stay the user's. Only an
agent whose ``backend`` is ``qwenpaw`` (the default) runs that model. QwenPaw
reads ``agent.json`` again when it changes and asks for the model every
turn.

Providers are files under ``<secret folder>/providers/``. A custom one is the
Provider model as QwenPaw's console saves it: ``id``, ``name``,
``base_url``, ``api_key``, ``chat_model`` (``OpenAIChatModel`` is Chat
Completions), ``is_custom`` and the models in ``extra_models``. Connecting
writes ``custom/openprogram.json`` at the gateway with a key naming QwenPaw
and one model per enabled model, and sets the agent's ``active_model`` to
``{"provider_id": "openprogram", "model": "<provider>/<model>"}``. QwenPaw
encrypts a plain-text ``api_key`` at its next start (``ENC:…``), which is
then kept while the key OpenProgram last wrote there (kept in the stash) is
still the one it would write; another is written over it. What the user
changes in that provider in QwenPaw's console (a model's window, its
generation settings, headers) stays. The provider file is shared by every
agent's row and goes once no connected row's stash names it and no agent's
``agent.json`` names the provider. QwenPaw reads provider files once, at start: the first connect needs
QwenPaw restarted ("Provider 'openprogram' not found" until then).

The agent's ``thinking_level`` (magpie's effort field) is not set here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from .base import ROOT, Agent, Wiring, Write

PROVIDER_ID = "openprogram"
DEFAULT = "default"
ENCRYPTED = "ENC:"
#: The stash record's field of the plain-text key OpenProgram last wrote.
KEY_WROTE = "key_wrote"
#: The thinking levels QwenPaw names (thinking.py ThinkingControl.efforts), weakest first.
LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
#: The keys of one model in extra_models that are OpenProgram's to say,
#: unless the user changed one in QwenPaw (its config_overrides names it).
MODEL_OWNED = ("id", "name", "source", "supports_image", "supports_multimodal", "supports_tool_calling",
               "max_input_length", "max_input_length_configured", "max_output_length",
               "thinking_param_style", "reasoning_effort_options")
#: The provider file's keys that say it is OpenProgram's at this gateway;
#: the rest QwenPaw or its console rewrite (``Write.loose``).
OWNED = ("id", "base_url", "chat_model", "is_custom")
_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])$")


def _tilde(value: str) -> Path:
    if value == "~":
        return Path.home()
    if value.startswith("~/") or value.startswith("~\\"):
        return Path.home() / value[2:]
    return Path(value)


def working() -> Path:
    value = os.environ.get("QWENPAW_WORKING_DIR", "").strip()
    if value:
        folder = _tilde(value)
    else:
        folder = Path.home() / ".qwenpaw"
        legacy = Path.home() / ".copaw"
        if legacy.exists():
            folder = legacy
    try:
        return folder.resolve() if folder.exists() else folder
    except OSError:
        return folder


def _dotenv_value(path: Path, name: str) -> str:
    """``name``'s value in a .env file as python-dotenv reads one; the last wins."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    found = ""
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'":
            end = value.find(value[0], 1)
            if end > 0:
                value = value[1:end]
        elif " #" in value:
            value = value[:value.index(" #")].strip()
        found = value
    return found


def secret(work: Path) -> Path:
    value = os.environ.get("QWENPAW_SECRET_DIR", "").strip() or _dotenv_value(work / ".env", "QWENPAW_SECRET_DIR")
    if not value:
        return Path(str(work) + ".secret")
    return _tilde(value)


def provider_path(work: Optional[Path] = None) -> Path:
    return secret(work or working()) / "providers" / "custom" / f"{PROVIDER_ID}.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def profiles(work: Path) -> list[tuple[str, Path]]:
    """QwenPaw's agents, ``(id, workspace)``, in ``agent_order``, the default
    first; those switched off are left out (magpie ``qwenpawProfiles``)."""
    default = (DEFAULT, work / "workspaces" / DEFAULT)
    config = _read_json(work / "config.json")
    if not isinstance(config, dict):
        return [default]
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    found = agents.get("profiles") if isinstance(agents.get("profiles"), dict) else {}

    def folder(agent_id: str) -> Path:
        entry = found.get(agent_id) if isinstance(found.get(agent_id), dict) else {}
        value = str(entry.get("workspace_dir") or "").strip()
        return _tilde(value) if value else work / "workspaces" / agent_id
    order = [str(i) for i in agents.get("agent_order") or []]
    order += sorted(k for k in found if k not in order)
    out = [(DEFAULT, folder(DEFAULT))]
    for agent_id in order:
        if agent_id == DEFAULT or not _ID.match(agent_id) or any(agent_id == o for o, _ in out):
            continue
        entry = found.get(agent_id)
        if isinstance(entry, dict) and entry.get("enabled") is False:
            continue
        out.append((agent_id, folder(agent_id)))
    return out


def backend(path: Path) -> str:
    data = _read_json(path)
    value = data.get("backend") if isinstance(data, dict) else None
    return (str(value).strip() if value else "") or "qwenpaw"


def efforts(levels: list[str]) -> list[str]:
    lowered = {str(level).lower() for level in levels}
    return [level for level in LEVELS if level in lowered]


def model_entry(model: dict[str, Any]) -> dict[str, Any]:
    """One model as QwenPaw's ModelInfo, added by the user. Every one takes
    tools; a window under QwenPaw's least (1000) or not known is left to its
    default. QwenPaw applies an agent's thinking level only to a model whose
    card says how it thinks: ``thinking_param_style`` ``effort`` sends it as
    ``reasoning_effort``, held to the model's levels (``None`` takes back
    what was said before)."""
    entry: dict[str, Any] = {"id": model["id"], "name": model.get("name") or model["id"], "source": "user",
                             "supports_image": bool(model.get("vision")),
                             "supports_multimodal": bool(model.get("vision")), "supports_tool_calling": True}
    window = int(model.get("context_window") or 0)
    if window >= 1000:
        entry["max_input_length"], entry["max_input_length_configured"] = window, True
    if model.get("max_tokens"):
        entry["max_output_length"] = int(model["max_tokens"])
    levels = efforts(model.get("thinking_levels") or [])
    entry["thinking_param_style"] = "effort" if levels else None
    entry["reasoning_effort_options"] = levels or None
    return entry


def extra_models(models: list[dict[str, Any]], had: Any) -> list[dict[str, Any]]:
    """The models, what QwenPaw or the user changed in them kept (magpie ``qwenpawProviderJSON``)."""
    was = {item.get("id"): item for item in (had if isinstance(had, list) else []) if isinstance(item, dict)}
    out = []
    for model in models:
        entry = model_entry(model)
        have = was.get(model["id"])
        if have is not None:
            overrides = have.get("config_overrides") if isinstance(have.get("config_overrides"), list) else []
            for key, value in have.items():
                # what QwenPaw filled in where OpenProgram says nothing stays too
                if key not in MODEL_OWNED or key in overrides or key not in entry:
                    entry[key] = value
        for key in [k for k, v in entry.items() if v is None and k not in (have or {})]:
            del entry[key]
        out.append(entry)
    return out


def _wire_for(workspace: Path):
    def wire(at: Wiring) -> list[Write]:
        from .. import edit
        from ..keys import token_for
        from .engine import AgentConfigError
        path = workspace / "agent.json"
        text = edit.ConfigText.read(path)
        if not text.exists:
            raise AgentConfigError(f"QwenPaw has no {path} yet: start QwenPaw once (qwenpaw app) so it writes "
                                   "its agents' settings, then connect it")
        doc = edit.load("json", text.text, str(path))
        kind = str(doc.get("backend") or "").strip() or "qwenpaw"
        if kind != "qwenpaw":
            raise AgentConfigError(f"this QwenPaw agent runs on the {kind} backend, which doesn't use QwenPaw's "
                                   "models; switch it to QwenPaw's own backend first")
        prov = provider_path()
        ptext = edit.ConfigText.read(prov)
        have = edit.load("json", ptext.text, str(prov)) if ptext.exists else {}
        keep: dict[str, Any] = {"id": PROVIDER_ID, "name": "OpenProgram", "base_url": at.base("openai-chat"),
                                "chat_model": "OpenAIChatModel", "is_custom": True}
        # one key for every agent's row: they share the one provider file.
        # An encrypted key can't be read back: it is kept while it is the one
        # OpenProgram last wrote in plain text (magpie unstashPeek(keyWrote)),
        # else it is another instance's or an old one and is written anew
        key = token_for("qwenpaw")
        if not (str(have.get("api_key") or "").startswith(ENCRYPTED) and key in _keys_wrote()):
            keep["api_key"] = key
        at.record[KEY_WROTE] = key
        for key, value in {"enabled": True, "support_model_discovery": False, "support_connection_check": False,
                           "models": [], "generate_kwargs": {}, "custom_headers": {},
                           "snapshot_schema_version": 2}.items():
            if key not in have:
                keep[key] = value
        keep["extra_models"] = extra_models(at.models, have.get("extra_models"))
        current = doc.get("active_model")
        if isinstance(current, dict):
            sets: dict[str, Any] = {"active_model.provider_id": PROVIDER_ID, "active_model.model": at.model}
        else:
            sets = {"active_model": {"provider_id": PROVIDER_ID, "model": at.model}}
        return [
            # the provider file is OpenProgram's whole and every agent's (Write.shared): it goes
            # when the last agent whose stash names it disconnects. QwenPaw encrypts the key and
            # its console changes the rest; only where it points (id, base_url, chat_model,
            # is_custom) says the file is still this gateway's.
            Write(prov, "json", sets=keep, listing=True, owns=(ROOT,), shared=True,
                  loose=tuple(k for k in keep if k not in OWNED)),
            Write(path, "json", sets=sets),
        ]
    return wire


def _keys_wrote() -> set[str]:
    """The plain-text keys OpenProgram last wrote in the provider file, as any
    QwenPaw row's stash record keeps it."""
    from .. import stash
    return {str(record.get(KEY_WROTE)) for agent_id, record in stash.load().items()
            if (agent_id == "qwenpaw" or agent_id.startswith("qwenpaw#")) and isinstance(record, dict)
            and record.get(KEY_WROTE)}


def names_provider(prov: Path) -> bool:
    """An agent of QwenPaw's still names OpenProgram's provider: its active
    model, its subagent model or a fallback (magpie ``qwenpawNamesMagpie``)."""
    work = working()
    if prov != provider_path(work):
        return False
    for _agent_id, workspace in profiles(work):
        data = _read_json(workspace / "agent.json")
        if not isinstance(data, dict):
            continue
        named = [data.get("active_model"), data.get("subagent_model"),
                 *(data.get("fallback_models") if isinstance(data.get("fallback_models"), list) else [])]
        if any(isinstance(model, dict) and model.get("provider_id") == PROVIDER_ID for model in named):
            return True
    return False


def _notice() -> str:
    return ("QwenPaw reads its providers at start-up — restart QwenPaw once after connecting it, and after "
            "OpenProgram's models change; a model picked for an agent after that is used from its next message.")


def agent() -> Agent:
    return Agent(
        id="qwenpaw", name="QwenPaw", icon="qwenpaw", kind="app", protocol="openai-chat", bin="qwenpaw",
        dir=working, path=lambda: profiles(working())[0][1] / "agent.json",
        wire=lambda at: _wire_for(profiles(working())[0][1])(at), notice=_notice, shared_used=names_provider,
    )


def extra_agents() -> list[Agent]:
    """QwenPaw's other agents on the gateway, a row each."""
    work = working()
    rows: list[Agent] = []
    if not work.is_dir():
        return rows
    for agent_id, workspace in profiles(work)[1:]:
        path = workspace / "agent.json"
        if not path.is_file() or backend(path) != "qwenpaw":
            continue
        data = _read_json(path)
        name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
        rows.append(Agent(
            id=f"qwenpaw#{agent_id}", name=f"QwenPaw · {name or agent_id}", icon="qwenpaw", kind="app",
            protocol="openai-chat", path=lambda p=path: p, wire=_wire_for(workspace),
            notice=_notice, shared_used=names_provider,
        ))
    return rows
