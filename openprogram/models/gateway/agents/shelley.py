"""Shelley (boldsoftware/shelley), a coding agent web app, through its own API.

After yetone/magpie ``internal/agent/shelley.go`` (MIT License, Copyright (c)
yetone). Shelley's models are in its database, not in a file OpenProgram can
edit. A running Shelley is told through the API its own Models and Profiles
pages use, on the Unix socket it opens for its CLI: ``$SHELLEY_SOCKET``,
else ``$XDG_CONFIG_HOME/shelley/shelley.sock`` (``~/.config/shelley``), or
``shelley-2.sock`` … when a second server took the first.

  GET    /api/custom-models       [{model_id, display_name, provider_type, endpoint, model_name, …}]
  POST   /api/custom-models       {display_name, provider_type, endpoint, api_key, model_name, …} → the model
  PUT    /api/custom-models/{id}  the same fields
  DELETE /api/custom-models/{id}
  GET    /api/models              every model Shelley can use, {id, display_name, ready}
  GET    /api/profiles            [{name, default, model, thinking_level, …}]
  PUT    /api/profiles/{name}     the profile back whole

A model of OpenProgram's is a custom model of type openai at the gateway's
Chat Completions base with Shelley's own key, one per model picked; the ids
OpenProgram made are kept, so a model the user made by hand stays theirs.
Picking one makes it the default profile's model, which new conversations
start on; what the profile had is kept and put back on disconnect, and
OpenProgram's models go.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import local_api
from .base import Agent, AppGone, Field, Live, Wiring, find_tool

DOWN = "Shelley isn't running — start it (shelley serve) and try again"


class _Down(Exception):
    """No Shelley answers."""


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "shelley"


def sockets() -> list[str]:
    """The sockets a running Shelley may answer on, in the order its CLI tries them."""
    if os.environ.get("SHELLEY_SOCKET"):
        return [os.environ["SHELLEY_SOCKET"]]
    folder = config_dir()
    return [str(folder / "shelley.sock"), *(str(folder / f"shelley-{i}.sock") for i in range(2, 6))]


def call(method: str, path: str, body: Any = None) -> Any:
    from .engine import AgentConfigError
    for sock in sockets():
        if not os.path.exists(sock):
            continue
        try:
            return local_api.request(method, "http://shelley" + path, body, unix=sock, timeout=3)
        except local_api.NotRunning:
            continue  # a socket a stopped Shelley left behind
        except local_api.ApiError as exc:
            raise AgentConfigError(f"Shelley: {method} {path}: {exc.status} {exc}") from exc
    raise _Down(DOWN)


def custom_models() -> list[dict[str, Any]]:
    data = call("GET", "/api/custom-models")
    return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []


def mine(models: list[dict[str, Any]], kept: dict[str, Any]) -> list[dict[str, Any]]:
    ids = kept.get("models") or []
    return [m for m in models if m.get("model_id") in ids]


def default_profile() -> dict[str, Any]:
    from .engine import AgentConfigError
    data = call("GET", "/api/profiles")
    for profile in data if isinstance(data, list) else []:
        if isinstance(profile, dict) and profile.get("default") is True:
            return profile
    raise AgentConfigError("Shelley has no default profile")


def put_default(profile: dict[str, Any]) -> None:
    call("PUT", "/api/profiles/" + quote(str(profile.get("name") or ""), safe=""), profile)


def _row(at: Wiring, ref: str) -> dict[str, Any]:
    return next((row for row in at.models if row["id"] == ref), {})


def _reasons(row: dict[str, Any]) -> bool:
    return bool(row.get("reasoning") or [t for t in row.get("thinking_levels") or [] if t != "off"])


def body(at: Wiring, ref: str) -> dict[str, Any]:
    """What Shelley is sent of model ``ref``: Shelley knows a model's reasoning
    and images from models.dev by its endpoint and name, which OpenProgram's
    are neither, so what the catalog knows is said."""
    out: dict[str, Any] = {"display_name": f"{ref} (OpenProgram)", "provider_type": "openai",
                           "endpoint": at.base("openai-chat"), "api_key": at.key, "model_name": ref}
    row = _row(at, ref)
    if row:
        out["reasoning_support"] = "yes" if _reasons(row) else "no"
        out["image_support"] = "yes" if row.get("vision") else "no"
        if int(row.get("max_tokens") or 0) > 0:
            out["max_tokens"] = int(row["max_tokens"])
    return out


def rewire(at: Wiring, models: list[dict[str, Any]], kept: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenProgram's models in Shelley brought to the gateway as it is now;
    those gone from Shelley forgotten."""
    ours = mine(models, kept)
    for m in ours:
        call("PUT", "/api/custom-models/" + quote(m["model_id"], safe=""), body(at, m.get("model_name") or ""))
    kept["models"] = [m["model_id"] for m in ours]
    return ours


def ready(model_id: str) -> bool:
    """Shelley can use ``model_id`` now; not known is ready (Shelley refuses one it can't use)."""
    try:
        data = call("GET", "/api/models")
    except Exception:  # noqa: BLE001 - not known
        return True
    return any(isinstance(m, dict) and m.get("id") == model_id and m.get("ready") for m in data or [])


def here() -> bool:
    """Shelley is installed: its command, or its config folder (made for its socket)."""
    return bool(find_tool("shelley")) or config_dir().is_dir()


# Live hooks

def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    kept = dict(kept)
    try:
        profile = default_profile()
        ours = rewire(at, custom_models(), kept)
        model_id = next((m["model_id"] for m in ours if m.get("model_name") == at.model), "")
        if model_id and at.same_pick:
            # connected on this model already (a moved port, a new key): its
            # models brought to the gateway above, a model picked in Shelley since stays
            return kept
        if not model_id:
            made = call("POST", "/api/custom-models", body(at, at.model))
            model_id = made.get("model_id") if isinstance(made, dict) else ""
            if not model_id:
                raise AgentConfigError("Shelley made the model but gave no id for it")
            kept["models"] = [*kept.get("models", []), model_id]
        if "was" not in kept:
            # what the profile had, before OpenProgram's first model
            kept["was"] = {"model": profile.get("model") or "", "thinking_level": profile.get("thinking_level") or ""}
        profile["model"] = model_id
        if not _reasons(_row(at, at.model)) and _row(at, at.model):
            profile["thinking_level"] = ""  # a level Shelley would refuse
        put_default(profile)
    except _Down as exc:
        raise AgentConfigError(str(exc)) from exc
    return kept


def _disconnect(kept: dict[str, Any]) -> None:
    """The default profile back on the model it had (the server's default when
    that is gone), and OpenProgram's models out."""
    from .engine import AgentConfigError
    try:
        profile = default_profile()
        models = custom_models()
    except _Down as exc:
        if not here():
            # Shelley uninstalled: OpenProgram's models come out once it is
            # back (magpie shelleyTakeOut)
            raise AppGone("Shelley is uninstalled") from exc
        raise AgentConfigError(str(exc)) from exc
    try:
        ours = mine(models, kept)
        was = kept.get("was")
        current = profile.get("model") or ""
        if any(m["model_id"] == current for m in ours):
            if was is not None:
                profile["model"] = was.get("model") or ""
                if not profile.get("thinking_level"):
                    profile["thinking_level"] = was.get("thinking_level") or ""
                if profile["model"] and not ready(profile["model"]):
                    profile["model"] = ""
            else:
                profile["model"] = ""
            if profile["model"] != current:
                put_default(profile)
        for m in ours:
            call("DELETE", "/api/custom-models/" + quote(m["model_id"], safe=""))
    except _Down as exc:
        raise AgentConfigError(str(exc)) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    kept = dict(kept)
    try:
        models = custom_models()
        if mine(models, kept):
            rewire(at, models, kept)
    except _Down:
        pass  # not running: nothing to keep current
    return kept


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    try:
        profile = default_profile()
        models = custom_models()
    except Exception:  # noqa: BLE001 - nothing to read
        return []
    for m in mine(models, kept):
        if m["model_id"] == profile.get("model") and m.get("endpoint") != at.base("openai-chat"):
            return ["custom-models.endpoint"]
    return []


def _notice() -> str:
    return ("New Shelley conversations start on this; an open one keeps its model until you pick another "
            "in its model menu.")


def agent() -> Agent:
    return Agent(
        id="shelley", name="Shelley", icon="shelley-color", kind="app", protocol="openai-chat",
        bin="shelley", dir=config_dir, path=lambda: config_dir() / "shelley.sock",
        ua=("shelley",), fields=[Field("model", "Model")], wire=lambda at: [],
        notice=_notice, live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
