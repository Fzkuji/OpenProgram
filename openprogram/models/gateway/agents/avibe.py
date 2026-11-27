"""Avibe (avibe-bot/avibe, the avibe-os package and its ``vibe`` CLI), a local agent OS.

After yetone/magpie ``internal/agent/avibe.go`` (MIT License, Copyright (c)
yetone). Its own agent, Vibey, and the Claude Code, Codex and OpenCode it
runs all take their models from its Model Hub, a gateway of its own in front
of the model sources on its Models page. Its home is ``$AVIBE_HOME``, else
``~/.avibe`` (``~/.vibe_remote`` before 3.0), the sources
``model_hub.sources`` in ``config/config.json`` there.

OpenProgram is one such source: an API-key source of the custom vendor at
the gateway's Chat Completions base with an agent key of Avibe's own. Avibe
lists its models from the gateway's ``/models`` itself, so the row is a
switch, with no model to pick. Chat Completions rather than Responses: for a
Responses source the Model Hub runs Codex's executor, which names itself
codex-tui and would be counted as Codex.

A source can't be written into config.json by hand (its key lives in the
Model Hub's store), so it is added and removed as Avibe's Models page does,
through Avibe's internal API while Avibe runs:

  POST /internal/model-hub  {"operation": "create_source"|"delete_source"|"refresh_source", "payload": {…}}

on the socket ``state/dispatch.sock`` in its home, or on Windows at the
loopback port ``runtime/control-ipc/endpoint.json`` gives, with its bearer
token. The source's id is kept, so a renamed one is still OpenProgram's.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from . import local_api
from .base import Agent, Live, Wiring

DOWN = "Avibe adds and removes model sources only while it runs: start it (vibe start), then try again"
PROBE = 1.5   # an Avibe that doesn't answer at once isn't running
WAIT = 30.0   # adding a source lists its models; removing one rewrites its route chains


def home() -> Path:
    """$AVIBE_HOME, else ~/.avibe, else the ~/.vibe_remote of an Avibe from before the move."""
    value = os.environ.get("AVIBE_HOME", "").strip()
    if value:
        if value == "~" or value.startswith(("~/", "~" + os.sep)):
            value = str(Path.home()) + value[1:]
        return Path(os.path.abspath(value))
    folder = Path.home() / ".avibe"
    if not os.path.lexists(folder) and (Path.home() / ".vibe_remote").is_dir():
        return Path.home() / ".vibe_remote"
    return folder


def config_path() -> Path:
    return home() / "config" / "config.json"


class AvibeError(Exception):
    """A refusal from Avibe's Model Hub, with what it would take away."""

    def __init__(self, code: str, detail: str = "", hops: Any = None, interrupts: Any = None):
        message = "Avibe: " + code
        if detail and detail != "modelHub.errors." + code:
            message += f" ({detail})"
        super().__init__(message)
        self.hops, self.interrupts = hops, interrupts


@dataclass
class Client:
    """A running Avibe's internal API."""

    url: str
    unix: str = ""
    token: str = ""

    def call(self, op: str, payload: dict[str, Any], timeout: float = WAIT) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            out = local_api.request("POST", self.url + "/internal/model-hub", {"operation": op, "payload": payload},
                                    headers=headers, unix=self.unix, timeout=timeout)
        except local_api.ApiError as exc:
            out = exc.body if isinstance(exc.body, dict) else {"ok": False, "error": str(exc)}
        if not isinstance(out, dict):
            raise AvibeError(f"{op}: the answer is not an object")
        if not out.get("ok"):
            raise AvibeError(str(out.get("error") or "refused"), str(out.get("detail") or ""),
                             out.get("would_remove_hops"), out.get("would_interrupt"))
        return out.get("result")

    def confirmed(self, op: str, payload: dict[str, Any]) -> None:
        """A change Avibe may ask to have confirmed first, as its Models page
        does when a source leaves routes behind: asked again with what it
        said it would take away. The user asked for it in OpenProgram."""
        payload["force"] = True
        try:
            self.call(op, payload)
            return
        except AvibeError as exc:
            if not exc.hops and not exc.interrupts:
                raise
            if exc.hops:
                payload["would_remove_hops"] = exc.hops
            if exc.interrupts:
                payload["would_interrupt"] = exc.interrupts
        self.call(op, payload)

    def create(self, at: Wiring) -> str:
        """Add OpenProgram's source, saved without Avibe's protocol probe (which
        can't tell the gateway's protocols apart), and give its id."""
        result = self.call("create_source", {"source": {
            "kind": "api_key", "vendor": "custom", "display_name": "openprogram",
            "base_url": at.base("openai-chat"), "key": at.key,
            "protocol": "openai_chat", "supply_channel": "hub", "billing": "metered",
            "save_unverified": True,
        }})
        source = result.get("source") if isinstance(result, dict) else None
        if not isinstance(source, dict) or not source.get("id"):
            raise AvibeError("Avibe added no source")
        return str(source["id"])


def client_at(folder: Path) -> Optional[Client]:
    """Where the Avibe at ``folder`` listens, as its own CLI finds it; ``None`` with nothing there."""
    if sys.platform == "win32":
        try:
            info = json.loads((folder / "runtime" / "control-ipc" / "endpoint.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        port = info.get("port") if isinstance(info, dict) else None
        if (not isinstance(info, dict) or info.get("transport") != "tcp" or info.get("host") != "127.0.0.1"
                or not isinstance(port, int) or not 0 < port <= 65535 or not info.get("bearer_token")):
            return None
        return Client(f"http://127.0.0.1:{port}", token=str(info["bearer_token"]))
    sock = folder / "state" / "dispatch.sock"
    try:
        if not stat.S_ISSOCK(sock.stat().st_mode):
            return None
    except OSError:
        return None
    return Client("http://localhost", unix=str(sock))


def live_client(folder: Path) -> Optional[Client]:
    """The internal API of the Avibe at ``folder`` if it answers; its socket outlives a crash."""
    client = client_at(folder)
    if client is None:
        return None
    try:
        client.call("list_sources", {}, timeout=PROBE)
    except (local_api.NotRunning, AvibeError):
        return None
    return client


def sources() -> list[dict[str, Any]]:
    try:
        doc = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    hub = doc.get("model_hub") if isinstance(doc, dict) else None
    found = hub.get("sources") if isinstance(hub, dict) else None
    return [s for s in found if isinstance(s, dict)] if isinstance(found, list) else []


def ours(v1: str, kept: dict[str, Any]) -> Optional[dict[str, Any]]:
    """OpenProgram's source: the one whose id was kept when it was added, else
    one of the custom vendor at the gateway (added by hand)."""
    found = sources()
    if kept.get("source"):
        for s in found:
            if s.get("id") == kept["source"]:
                return s
    for s in found:
        if s.get("id") and s.get("vendor") == "custom" and str(s.get("base_url") or "").rstrip("/") == v1:
            return s
    return None


def moved(base: str, v1: str) -> bool:
    """``base`` is the gateway's own on an older port, which a sync moves to
    where it is now; one on another machine or behind a proxy's path stays."""
    base = base.rstrip("/")
    if base == v1:
        return False
    try:
        was, now = urlparse(base), urlparse(v1)
    except ValueError:
        return False
    return (was.hostname in ("127.0.0.1", "localhost", "::1") and was.path == now.path
            and was.scheme == now.scheme and was.port != now.port)


def _wire(client: Client, at: Wiring, there: Optional[dict[str, Any]]) -> str:
    """Add the source, in place of one there: its key is sealed in the Model Hub
    and goes with the address, so a source on an old address is made again."""
    if there is not None:
        client.confirmed("delete_source", {"source_id": there["id"]})
    return client.create(at)


# Live hooks

def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    v1 = at.base("openai-chat")
    there = ours(v1, kept)
    if there is not None and str(there.get("base_url") or "").rstrip("/") == v1:
        return {**kept, "source": there["id"]}
    client = live_client(home())
    if client is None:
        raise AgentConfigError(DOWN)
    try:
        return {**kept, "source": _wire(client, at, there)}
    except (AvibeError, local_api.NotRunning) as exc:
        raise AgentConfigError(str(exc)) from exc


def _disconnect(kept: dict[str, Any]) -> None:
    from .engine import AgentConfigError
    found = sources()
    there = next((s for s in found if kept.get("source") and s.get("id") == kept["source"]), None)
    if there is None:
        return
    client = live_client(home())
    if client is None:
        raise AgentConfigError(DOWN)
    try:
        client.confirmed("delete_source", {"source_id": there["id"]})
    except (AvibeError, local_api.NotRunning) as exc:
        raise AgentConfigError(str(exc)) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    """The models picked reach the source when it lists the gateway's again;
    Avibe does that itself only for a listing older than half an hour."""
    from .engine import AgentConfigError
    v1 = at.base("openai-chat")
    there = ours(v1, kept)
    if there is None:
        return kept
    client = live_client(home())
    if client is None:
        return kept
    try:
        if moved(str(there.get("base_url") or ""), v1):
            return {**kept, "source": _wire(client, at, there)}
        client.confirmed("refresh_source", {"source_id": there["id"]})
    except (AvibeError, local_api.NotRunning) as exc:
        raise AgentConfigError(str(exc)) from exc
    return {**kept, "source": there["id"]}


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    there = ours(at.base("openai-chat"), kept)
    if there is not None and str(there.get("base_url") or "").rstrip("/") != at.base("openai-chat"):
        return ["model_hub.sources.base_url"]
    return []


def agent() -> Agent:
    return Agent(
        id="avibe", name="Avibe", icon="avibe", aliases=("vibe", "vibey", "avibe-os"), kind="app",
        protocol="openai-chat", dir=home, path=config_path, detect_only=True, detect=lambda: home().is_dir(),
        fields=[], wire=lambda at: [],
        live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
