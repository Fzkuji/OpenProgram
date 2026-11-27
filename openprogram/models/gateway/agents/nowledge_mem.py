"""Nowledge Mem (mem.nowledge.co, a Tauri app, ``co.nowledge.mem.desktop``).

After yetone/magpie ``internal/agent/nowledgemem.go`` (MIT License, Copyright
(c) yetone). Mem is a personal memory app whose own AI (search, distilling,
the chat) runs on a remote LLM picked in Settings > AI. Mem 0.10 ships a
provider type of magpie's own, ``magpie``, an OpenAI-compatible endpoint
(default ``http://127.0.0.1:3425/v1``): OpenProgram uses that provider as
Mem defines it, id and type ``magpie``, at OpenProgram's gateway with Mem's
own agent key.

Mem keeps its providers in ``remote_llm.json`` in its config folder
(``$NMEM_APP_CONFIG_DIR``, else the OS config folder's
``co.nowledge.mem.desktop``), each key encrypted there, so the file is never
written. Mem's backend changes it through its HTTP API on 127.0.0.1:14242
(``$NMEM_API_URL``), which takes loopback requests without a key::

    GET  /remote-llm/config            enabled, active_provider, providers{id: provider_type, model, api_base, …}
    POST /remote-llm/config            {enabled, provider, provider_type, model, api_base, api_key, temperature, max_tokens, timeout}
    POST /remote-llm/activate          {provider}
    POST /remote-llm/provider/remove   {provider}
    POST /remote-llm/enabled           {enabled}

``POST /remote-llm/config`` saves a provider and makes it the active one;
``api_base`` and ``api_key`` left out keep what was stored, but temperature,
max_tokens and timeout left out reset, so the provider's own are sent again
with every change. What Mem had before is kept: its active provider,
whether its AI was on, and a ``magpie`` provider the user made themselves.
Stepping out activates that provider again, puts the user's ``magpie`` back
or removes OpenProgram's, and turns the AI off again if it was off.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from . import local_api
from .base import Agent, App, Field, Live, Wiring, app_bundles
from .kun import loopback, moved, on_another_machine, host_of

BUNDLE = "co.nowledge.mem.desktop"
PROVIDER = "magpie"              # Mem's own provider type for magpie-style gateways
ADDR = "http://127.0.0.1:14242"
DEFAULT_BASE = "http://127.0.0.1:3425/v1"
PROBE = 1.5
WAIT = 20.0
DOWN = "Nowledge Mem changes its AI only while it runs: open Nowledge Mem, then try again"
APP = App(BUNDLE, ("Nowledge Mem.app",))


def config_dir() -> Path:
    raw = (os.environ.get("NMEM_APP_CONFIG_DIR") or "").strip()
    if raw:
        return Path(raw)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / BUNDLE
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / BUNDLE
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else home / ".config") / BUNDLE


def _path() -> Path:
    return config_dir() / "remote_llm.json"


class MemError(Exception):
    pass


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def do(self, method: str, path: str, body: Any = None, timeout: float = WAIT) -> Any:
        try:
            return local_api.request(method, self.base + path, body, timeout=timeout)
        except local_api.ApiError as exc:
            detail = exc.body.get("detail") if isinstance(exc.body, dict) else None
            if isinstance(detail, str) and detail:
                raise MemError("Nowledge Mem: " + detail) from exc
            raise MemError(f"Nowledge Mem: {method} {path}: HTTP {exc.status}") from exc
        except local_api.NotRunning as exc:
            raise MemError(f"Nowledge Mem: {exc}") from exc

    def config(self) -> dict[str, Any]:
        out = self.do("GET", "/remote-llm/config")
        return out if isinstance(out, dict) else {}

    def post(self, path: str, body: Any) -> None:
        self.do("POST", path, body)


def live_client() -> Optional[Client]:
    """Mem's backend if it answers: ``$NMEM_API_URL`` when it names this
    computer, else 127.0.0.1:14242."""
    base = ADDR
    raw = (os.environ.get("NMEM_API_URL") or "").strip()
    if raw:
        try:
            u = urlparse(raw)
        except ValueError:
            return None
        if u.scheme not in ("http", "https") or not loopback(u.hostname or ""):
            return None
        base = raw.rstrip("/")
    c = Client(base)
    try:
        c.do("GET", "/remote-llm/status", timeout=PROBE)
    except MemError:
        return None
    return c


def read_file() -> Optional[dict[str, Any]]:
    import json
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _now() -> tuple[dict[str, Any], Optional[Client]]:
    c = live_client()
    if c is not None:
        try:
            return c.config(), c
        except MemError:
            pass
    return read_file() or {}, None


def _kind(p: dict[str, Any]) -> str:
    return p.get("provider_type") or p.get("type") or ""


def base_of(p: dict[str, Any]) -> str:
    """Where ``p`` sends Mem's requests."""
    if not p.get("api_base") and _kind(p) == PROVIDER:
        return DEFAULT_BASE
    return (p.get("api_base") or "").rstrip("/")


def mem_on(s: dict[str, Any], stashed: bool, served: Optional[set[str]] = None) -> tuple[dict[str, Any], bool]:
    """The ``magpie`` provider is the one Mem's AI uses and it is OpenProgram's:
    one at another machine's address OpenProgram never set up is a gateway on
    a server the user added themselves, never this one's."""
    p = (s.get("providers") or {}).get(PROVIDER)
    if not isinstance(p, dict) or not s.get("enabled") or s.get("active_provider") != PROVIDER:
        return p or {}, False
    if not on_another_machine(base_of(p)):
        return p, True
    return p, stashed and (served is None or p.get("model") in served)


def elsewhere(p: dict[str, Any], v1: str) -> bool:
    """The ``magpie`` provider points at another machine than OpenProgram wrote:
    the user's since, never removed or written over on the way out."""
    b = base_of(p)
    return on_another_machine(b) and host_of(b) != host_of(v1)


def body_for(p: dict[str, Any], pid: str, model: str) -> dict[str, Any]:
    """The POST /remote-llm/config saving ``pid`` as ``p`` is, with ``model``,
    its parameters sent again (left out, Mem resets them)."""
    body: dict[str, Any] = {"enabled": True, "provider": pid, "provider_type": _kind(p) or pid, "model": model}
    for key in ("temperature", "max_tokens", "timeout"):
        if p.get(key) is not None:
            body[key] = p[key]
    return body


def _err(exc: Exception):
    from .engine import AgentConfigError
    return AgentConfigError(str(exc))


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    s, c = _now()
    if c is None:
        raise AgentConfigError(DOWN)
    kept = dict(kept)
    providers = s.get("providers") or {}
    had = providers.get(PROVIDER) if isinstance(providers.get(PROVIDER), dict) else None
    if "prev" not in kept:
        prev: dict[str, Any] = {"enabled": bool(s.get("enabled"))}
        if s.get("active_provider") and s.get("active_provider") != PROVIDER:
            prev["active"] = s["active_provider"]
        if had is not None:
            prev["had"] = had
        kept["prev"] = prev
    # connected on this model already (a moved port, a new key): the address
    # and key only; a model, provider or AI switch picked in Mem since stays
    keep = at.same_pick and had is not None
    body = body_for(had or {}, PROVIDER, ((had or {}).get("model") or at.model) if keep else at.model)
    body["api_base"] = at.base("openai-chat")
    if kept["prev"].get("had") is None:
        # a key on the user's own "magpie" provider stays theirs
        body["api_key"] = at.key
    active, enabled = s.get("active_provider") or "", bool(s.get("enabled"))
    try:
        c.post("/remote-llm/config", body)
        if keep and active and active != PROVIDER:
            # saving a provider makes it Mem's active one
            c.post("/remote-llm/activate", {"provider": active})
        if keep and not enabled:
            c.post("/remote-llm/enabled", {"enabled": False})
    except MemError as exc:
        raise _err(exc) from exc
    return kept


def _unwire(c: Client, s: dict[str, Any], kept: dict[str, Any]) -> None:
    providers = s.get("providers") or {}
    prev = kept.get("prev")
    if prev is None:
        # nothing kept: a "magpie" the user made by hand is theirs to keep;
        # Mem goes to another provider of its own
        prev = {"had": {}, "enabled": True}
        others = sorted(pid for pid in providers if pid != PROVIDER)
        if others:
            prev["active"] = others[0]
        else:
            prev["enabled"] = False
    had = prev.get("had")
    p = providers.get(PROVIDER)
    if isinstance(p, dict) and had and had.get("model"):
        back = body_for(p, PROVIDER, had["model"])
        back["api_base"] = had.get("api_base") or ""
        for key in ("temperature", "max_tokens", "timeout"):
            if had.get(key) is not None:
                back[key] = had[key]
        c.post("/remote-llm/config", back)
    # a provider the user picked in Mem since stays picked
    mine = s.get("active_provider") == PROVIDER
    active = prev.get("active") or ""
    if active in providers and active != PROVIDER and mine:
        c.post("/remote-llm/activate", {"provider": active})
    if isinstance(p, dict) and had is None:
        c.post("/remote-llm/provider/remove", {"provider": PROVIDER})
    if not prev.get("enabled") and mine:
        c.post("/remote-llm/enabled", {"enabled": False})


def _disconnect(kept: dict[str, Any]) -> None:
    from .engine import AgentConfigError
    s, c = _now()
    v1 = _v1()
    p = (s.get("providers") or {}).get(PROVIDER)
    if isinstance(p, dict) and v1 and elsewhere(p, v1):
        # the user pointed it at another machine since: theirs, left as it is
        return
    stashed = "prev" in kept
    _, on = mem_on(s, stashed)
    if not on and not (stashed and isinstance(p, dict)):
        return
    if c is None:
        raise AgentConfigError(DOWN)
    try:
        _unwire(c, s, kept)
    except MemError as exc:
        raise _err(exc) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    """A gateway on a new port reaches the provider."""
    c = live_client()
    if c is None:
        return kept
    try:
        s = c.config()
        p, on = mem_on(s, "prev" in kept)
        if not on or not moved(base_of(p), at.base("openai-chat")):
            return kept
        body = body_for(p, PROVIDER, p.get("model") or at.model)
        body["api_base"] = at.base("openai-chat")
        c.post("/remote-llm/config", body)
    except MemError as exc:
        raise _err(exc) from exc
    return kept


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    s, _ = _now()
    p, on = mem_on(s, "prev" in kept)
    if not on:
        return []
    from .kun import same_gateway
    return [] if same_gateway(base_of(p), at.base("openai-chat")) else ["api_base"]


def _v1() -> str:
    from .engine import wiring
    try:
        return wiring(agent(), {}).base("openai-chat")
    except Exception:  # noqa: BLE001
        return ""


def _detect() -> bool:
    return config_dir().is_dir() or bool(app_bundles(APP))


def agent() -> Agent:
    return Agent(
        id="nowledge_mem", name="Nowledge Mem", icon="nowledge-mem", kind="app", protocol="openai-chat",
        aliases=("nowledge-mem", "nmem"), path=_path, dir=config_dir, detect_only=True, detect=_detect,
        fields=[Field("model", "Model")],
        wire=lambda at: [], live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
