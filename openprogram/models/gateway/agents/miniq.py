"""miniQ (LeadingThink/miniQ), a desktop AI coworker and its ``miniq`` client.

After yetone/magpie ``internal/agent/miniq.go`` (MIT License, Copyright (c)
yetone). Both are clients of one miniq-daemon, whose one provider is
settings.json's ``provider``, ``{baseUrl, apiKey, model, apiProtocol,
reasoningEffort?}``, in the daemon's data folder: ``$MINIQ_DATA_DIR``, else
the folder ``miniq-data-dir.json`` beside the default names, else
``%LOCALAPPDATA%\\miniq`` or ``~/.local/share/miniq`` (macOS too).

Connecting writes the provider as the gateway's Chat Completions base with
miniQ's own key (it sends no User-Agent of its own), the model's catalog id
and ``apiProtocol`` ``chat_completions``: ``auto`` would guess Responses or
Anthropic Messages from a model's name. The user's provider is stashed whole
and put back on disconnect; the rest of settings.json stays as it is.

The daemon reads settings.json once, at its start, and writes its own copy
back on every settings change, so a running one is told too: its
``settings.update`` RPC over the local WebSocket ``daemon.json`` names, as
the desktop's Settings does. A daemon that can't be told keeps the old
provider until it restarts, which the notice says.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from .base import Agent, Field, Live, Wiring, Write

PROTOCOL = "chat_completions"
_log = logging.getLogger(__name__)


def data_dir() -> Path:
    """miniQ's data folder, as miniq-local's data_dir finds it."""
    if os.environ.get("MINIQ_DATA_DIR"):
        return Path(os.environ["MINIQ_DATA_DIR"])
    default = Path.home() / ".local" / "share" / "miniq"
    if os.environ.get("LOCALAPPDATA"):
        default = Path(os.environ["LOCALAPPDATA"]) / "miniq"
    try:
        pointer = json.loads((default.parent / "miniq-data-dir.json").read_text(encoding="utf-8"))
        if isinstance(pointer, dict) and isinstance(pointer.get("data_dir"), str) and pointer["data_dir"]:
            return Path(pointer["data_dir"])
    except (OSError, ValueError):
        pass
    return default


def settings_path() -> Path:
    return data_dir() / "settings.json"


def rpc(folder: Path, method: str, params: Any) -> tuple[Any, bool]:
    """``(result, reached)`` of ``method`` on the daemon whose data folder is
    ``folder``; not reached is no daemon to ask (none running, or a
    daemon.json a daemon that didn't exit cleanly left behind). A refusal
    raises ``RuntimeError``."""
    try:
        info = json.loads((folder / "daemon.json").read_text(encoding="utf-8"))
        port, token = int(info.get("port") or 0), str(info.get("token") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        return None, False
    if not port or not token:
        return None, False
    from websockets.sync.client import connect
    try:
        ws = connect(f"ws://127.0.0.1:{port}/ws?token={quote(token, safe='')}", open_timeout=3,
                     max_size=8 << 20, proxy=None)
    except Exception:  # noqa: BLE001 - nothing answers: no daemon
        return None, False
    with ws:
        ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        while True:
            message = json.loads(ws.recv(timeout=3))
            if not isinstance(message, dict) or "jsonrpc" not in message or message.get("id") != 1:
                continue  # an event the daemon sends every client
            if message.get("error"):
                error = message["error"]
                raise RuntimeError(f"miniQ refused {method}: {error.get('message') if isinstance(error, dict) else error}")
            return message.get("result"), True


def push_of(provider: dict[str, Any]) -> dict[str, Any]:
    """settings.update's provider params for a provider as settings.json has it."""
    out = {"baseUrl": provider.get("baseUrl") or "", "model": provider.get("model") or "",
           "apiProtocol": provider.get("apiProtocol") or "auto"}
    if provider.get("apiKey"):
        out["apiKey"] = provider["apiKey"]
    else:
        out["clearApiKey"] = True
    return out


def _push(folder: Path, provider: dict[str, Any]) -> None:
    """Tell a running daemon the provider written; one that can't be told is left to the notice."""
    try:
        rpc(folder, "settings.update", {"provider": provider})
    except Exception as exc:  # noqa: BLE001 - logged; the notice says to restart
        _log.info("miniQ: %s", exc)


def _provider_in_file() -> Optional[dict[str, Any]]:
    from .. import edit
    try:
        doc = edit.load("json", edit.ConfigText.read(settings_path()).text, str(settings_path()))
    except edit.ConfigUnreadable:
        return None
    value = doc.get("provider")
    return value if isinstance(value, dict) else None


def provider(at: Wiring) -> dict[str, Any]:
    return {"baseUrl": at.base("openai-chat"), "apiKey": at.key, "model": at.model, "apiProtocol": PROTOCOL}


def wanted(at: Wiring) -> dict[str, Any]:
    """The provider written: a reconnect on the same pick (a new port, a new
    key) keeps a model picked in miniQ since."""
    want = provider(at)
    if at.same_pick:
        now = _provider_in_file() or {}
        if isinstance(now.get("model"), str) and now["model"]:
            want["model"] = now["model"]
    return want


def _wire(at: Wiring) -> list[Write]:
    """OpenProgram's provider, its whole (``owns``), the model in it the one
    miniQ's own picker rewrites (``loose``)."""
    want = wanted(at)
    return [Write(settings_path(), "json", sets={"provider": want}, owns=("provider",), loose=("provider.model",))]


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    kept = dict(kept)
    if "was" not in kept:
        # what the user had, for telling the daemon when it goes back
        kept["was"] = _provider_in_file()
    # what _wire writes (the file is read before it is written), so the
    # running daemon and the file agree
    _push(data_dir(), wanted(at))
    return kept


def _disconnect(kept: dict[str, Any]) -> None:
    # the file goes back through the stash; a running daemon is told the
    # user's provider (settings.update can't take one out, so with none of
    # theirs it keeps OpenProgram's until it restarts, which the notice says)
    was = kept.get("was")
    if isinstance(was, dict):
        _push(data_dir(), push_of(was))


def _notice() -> str:
    try:
        result, reached = rpc(data_dir(), "settings.get", {})
    except Exception:  # noqa: BLE001
        return ""
    if not reached or not isinstance(result, dict):
        return ""
    running = result.get("provider") if isinstance(result.get("provider"), dict) else {}
    written = _provider_in_file() or {}
    if (str(running.get("baseUrl") or "").rstrip("/") != str(written.get("baseUrl") or "").rstrip("/")
            or running.get("model") != written.get("model")):
        return "Restart miniQ to use this: its running daemon keeps the provider it started with."
    return ""


def agent() -> Agent:
    return Agent(
        id="miniq", name="miniQ", icon="miniq-color", kind="app", protocol="openai-chat",
        bin="miniq", dir=data_dir, path=settings_path,
        fields=[Field("model", "Model")], wire=_wire, notice=_notice,
        live=Live(connect=_connect, disconnect=_disconnect),
    )
