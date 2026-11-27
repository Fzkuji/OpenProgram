"""Kun (KunAgent/Kun, kun-agent.com): a local-first AI workbench whose desktop
app and ``kun`` TUI are clients of one runtime, ``kun serve``.

After yetone/magpie ``internal/agent/kun.go`` (MIT License, Copyright (c)
yetone). Kun's models come from the provider connections in the runtime's
registry, ``model-connections.v2.json`` in its data folder (``$KUN_DATA_DIR``,
else ``~/.kun/data``). The registry is the authority, and a connection's key
lives in Kun's protected credential store under a reference, so a
connection can't be written into a file by hand. OpenProgram adds and
removes its connection as Kun's own Settings > Providers and ``kun
provider`` do, through the runtime's admin API, while Kun runs::

    GET    /v1/model-connections                  the snapshot, with its revision
    POST   /v1/model-connections/connect          {expectedRevision, id, name, baseUrl, credential, models, …}
    PATCH  /v1/model-connections/:id             {expectedRevision, models}
    POST   /v1/model-connections/select           {expectedRevision, providerId, model}
    DELETE /v1/model-connections/:id?expected_revision=N
    POST   /v1/provider-config/transactions/preview, then commit   the default's own connection

at the ``baseUrl`` of ``runtime.json`` in the data folder, with its
``runtimeToken`` as a bearer, only on loopback. The connection,
``openprogram``, is an HTTP one at the gateway's Chat Completions base with
Kun's own agent key, listing the enabled models (at most 500). Picking a
model makes it Kun's default; the default Kun had is kept and picked again
when OpenProgram steps out, before its connection is removed. The
connection's id is kept too, so one Kun renamed is still OpenProgram's.

The loopback helpers here (:func:`loopback`, :func:`moved`,
:func:`same_gateway`, :func:`on_another_machine`) are shared by the other
running-app agents (``nowledge_mem.py``).
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

from . import local_api
from .base import Agent, App, Field, Live, Wiring, app_bundles

ID = "openprogram"
REGISTRY = "model-connections.v2.json"
MAX_MODELS = 500
PROBE = 1.5
WAIT = 30.0

DOWN = "Kun adds and changes model providers only while it runs: open Kun, then try again"
APP = App("com.xingyuzhong.deepseekgui", ("Kun.app",))  # magpie desktopapp.go


# Loopback helpers (magpie kunLoopback, onAnotherMachine, sameGateway, avibeMoved)

def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def on_another_machine(base: str) -> bool:
    """``base`` is an http(s) address on a host that isn't this computer."""
    try:
        u = urlparse(base)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname
    if host == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not ip.is_loopback and not ip.is_unspecified


def same_gateway(base: str, v1: str) -> bool:
    def norm(u: str) -> str:
        return u.strip().rstrip("/").replace("://localhost:", "://127.0.0.1:", 1)
    return bool(base) and norm(base) == norm(v1)


def host_of(u: str) -> str:
    try:
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def moved(base: str, now: str) -> bool:
    """``base`` is the gateway's own address on an older port, which a sync
    moves to where the gateway is now; one the user pointed elsewhere
    (another machine, a proxy's route) stays (magpie ``avibeMoved``)."""
    base = (base or "").rstrip("/")
    if not base or same_gateway(base, now):
        return False
    try:
        b, n = urlparse(base), urlparse(now)
        b_port, n_port = b.port, n.port
    except ValueError:
        return False
    if b.scheme not in ("http", "https") or not b.hostname or not loopback(b.hostname):
        return False
    return b.path.rstrip("/") == n.path.rstrip("/") and b_port != n_port


# Kun

def data_dir() -> Path:
    raw = (os.environ.get("KUN_DATA_DIR") or "").strip()
    if raw:
        return Path(os.path.abspath(raw))
    return Path.home() / ".kun" / "data"


class Client:
    """A running Kun runtime's admin API."""

    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def do(self, method: str, path: str, body: Any = None, timeout: float = WAIT) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            return local_api.request(method, self.base + path, body, headers=headers, timeout=timeout)
        except local_api.ApiError as exc:
            raise KunError(f"Kun: {exc}") from exc
        except local_api.NotRunning as exc:
            raise KunError(f"Kun: {exc}") from exc

    def snapshot(self) -> dict[str, Any]:
        return self.do("GET", "/v1/model-connections") or {}

    def connect(self, s: dict[str, Any], at: Wiring, m: str, select: bool = True) -> dict[str, Any]:
        # Kun's own probe is off: it can't tell the gateway's protocols apart
        # (it serves them all), and the models are the ones picked
        return self.do("POST", "/v1/model-connections/connect", {
            "expectedRevision": s.get("revision", 0),
            "id": ID, "name": "OpenProgram",
            "kind": "http", "authType": "api-key",
            "baseUrl": at.base("openai-chat"), "endpointFormat": "chat_completions",
            "credential": at.key,
            "models": models_for(at, m), "selectedModel": m,
            "probe": False, "select": select,
        }) or {}

    def patch_models(self, s: dict[str, Any], cid: str, models: list[str]) -> dict[str, Any]:
        return self.do("PATCH", "/v1/model-connections/" + quote(cid, safe=""),
                       {"expectedRevision": s.get("revision", 0), "models": models}) or {}

    def select(self, s: dict[str, Any], cid: str, m: str) -> None:
        self.do("POST", "/v1/model-connections/select",
                {"expectedRevision": s.get("revision", 0), "providerId": cid, "model": m})

    def remove(self, s: dict[str, Any], cid: str) -> dict[str, Any]:
        """Take connection ``cid`` out. Kun refuses to delete the connection its
        default names, so that one goes as Kun's own Settings retires it: one
        provider-config transaction that clears the default and removes it,
        previewed and then committed."""
        if s.get("defaultProviderId") != cid:
            return self.do("DELETE", f"/v1/model-connections/{quote(cid, safe='')}"
                                     f"?expected_revision={s.get('revision', 0)}") or {}
        preview = self.do("POST", "/v1/provider-config/transactions/preview", {
            "expectedRevision": s.get("revision", 0),
            "operations": [{"kind": "set-default-selection"},
                           {"kind": "remove-connection", "connectionId": cid}],
        }) or {}
        pid = preview.get("previewId") if isinstance(preview, dict) else None
        if not pid:
            raise KunError(f"Kun: no preview of removing {cid}")
        self.do("POST", "/v1/provider-config/transactions/commit", {
            "expectedRevision": preview.get("expectedRevision"), "previewId": pid,
            "idempotencyKey": f"openprogram:remove:{cid}:{pid}",
        })
        return self.snapshot()


class KunError(Exception):
    pass


def live_client(folder: Path) -> Optional[Client]:
    """The admin API of the Kun runtime serving ``folder`` if it answers there
    (``runtime.json`` outlives a crash), only on loopback: its token goes
    nowhere else."""
    try:
        d = json.loads((folder / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    base = d.get("baseUrl") if isinstance(d, dict) else None
    if not isinstance(base, str) or not base:
        return None
    try:
        u = urlparse(base)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not loopback(u.hostname or ""):
        return None
    c = Client(base, str(d.get("runtimeToken") or ""))
    try:
        c.do("GET", "/v1/model-connections", timeout=PROBE)
    except KunError:
        return None
    return c


def read_registry(folder: Path) -> Optional[dict[str, Any]]:
    """Kun's connections as its registry file has them (a map of profiles)."""
    try:
        doc = json.loads((folder / REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    providers = []
    for pid, p in sorted((doc.get("profiles") or {}).items()):
        if isinstance(p, dict):
            providers.append({**p, "id": p.get("id") or pid})
    return {"revision": doc.get("revision", 0), "providers": providers,
            "defaultProviderId": doc.get("defaultProviderId", ""),
            "defaultAccountId": doc.get("defaultAccountId", ""), "defaultModel": doc.get("defaultModel", "")}


def _provider(s: dict[str, Any], pid: str) -> Optional[dict[str, Any]]:
    return next((p for p in s.get("providers") or [] if p.get("id") == pid), None)


def ours(s: dict[str, Any], kept: dict[str, Any], v1: str) -> Optional[dict[str, Any]]:
    """OpenProgram's connection: the one whose id was kept, else an HTTP one at
    the gateway (added by hand in Kun's Settings)."""
    if kept.get("id"):
        found = _provider(s, kept["id"])
        if found:
            return found
    for p in s.get("providers") or []:
        if p.get("kind") in (None, "", "http") and same_gateway(p.get("baseUrl") or "", v1):
            return p
    return None


def models_for(at: Wiring, want: str) -> list[str]:
    """The enabled models, as many as a connection takes, with ``want`` among them."""
    out: list[str] = []
    for row in at.models:
        mid = row.get("id") or ""
        if mid and len(mid) <= 512 and mid not in out:
            out.append(mid)
    if want and want not in out:
        out.insert(0, want)
    if len(out) > MAX_MODELS:
        if out.index(want) >= MAX_MODELS:
            out[MAX_MODELS - 1] = want
        out = out[:MAX_MODELS]
    return out


def _err(exc: Exception):
    from .engine import AgentConfigError
    return AgentConfigError(str(exc))


def _key_stale(at: Wiring) -> bool:
    """The connection holds another key than Kun is given now."""
    from .engine import key_mark
    return bool(at.previous) and at.previous.get("key") != key_mark(at.key)


def _wire(c: Client, s: dict[str, Any], at: Wiring, m: str, kept: dict[str, Any], *,
          keep: bool = False) -> dict[str, Any]:
    """OpenProgram's connection at the gateway with model ``m`` Kun's default.
    With ``keep`` (connected on this model already: a moved port, a new key)
    only its address and key change, and Kun's default and the connection's
    own pick stay as they are in Kun."""
    kept = dict(kept)
    v1 = at.base("openai-chat")
    mine = ours(s, kept, v1)
    if mine and keep:
        kept["id"] = mine["id"]
        default = (s.get("defaultModel") or "") if s.get("defaultProviderId") == mine["id"] else ""
        pick = default or mine.get("selectedModel") or m
        if moved(mine.get("baseUrl") or "", v1) or _key_stale(at):
            # Kun takes a connection's address and key only as it is added:
            # added again as it was, Kun's default again if it was this one
            s = c.remove(s, mine["id"])
            after = c.connect(s, at, pick, select=bool(default))
            kept["id"] = next((p.get("id") for p in after.get("providers") or []
                               if _provider(s, p.get("id")) is None), mine["id"])
            return kept
        models = models_for(at, pick)
        if models != list(mine.get("models") or []):
            c.patch_models(s, mine["id"], models)
        return kept
    if mine and moved(mine.get("baseUrl") or "", v1):
        s = c.remove(s, mine["id"])
        mine = None
    if mine is None:
        if s.get("defaultProviderId") and "prev" not in kept:
            kept["prev"] = {"provider": s.get("defaultProviderId"), "account": s.get("defaultAccountId") or "",
                            "model": s.get("defaultModel") or ""}
        after = c.connect(s, at, m)
        for p in after.get("providers") or []:
            if _provider(s, p.get("id")) is None:
                kept["id"] = p.get("id")
                break
        kept.setdefault("id", ID)
        return kept
    kept["id"] = mine["id"]
    models = models_for(at, m)
    if models != list(mine.get("models") or []):
        s = c.patch_models(s, mine["id"], models)
    if s.get("defaultProviderId") == mine["id"] and s.get("defaultModel") == m:
        return kept
    c.select(s, mine["id"], m)
    return kept


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    c = live_client(data_dir())
    if c is None:
        raise AgentConfigError(DOWN)
    try:
        return _wire(c, c.snapshot(), at, at.model, kept, keep=at.same_pick)
    except KunError as exc:
        raise _err(exc) from exc


def _disconnect(kept: dict[str, Any]) -> None:
    from .engine import AgentConfigError
    folder = data_dir()
    v1 = _v1()
    known = read_registry(folder)
    c = live_client(folder)
    if c is None:
        if known is None:
            return
        if ours(known, kept, v1) is None:
            return
        raise AgentConfigError(DOWN)
    try:
        s = c.snapshot()
        mine = ours(s, kept, v1)
        if mine is None:
            return
        prev = kept.get("prev") or {}
        if not _provider(s, prev.get("provider") or "") or prev.get("provider") == mine["id"]:
            prev = {}
        if prev.get("provider") and prev.get("model") and s.get("defaultProviderId") == mine["id"]:
            # Kun's default back while it is still OpenProgram's: one the
            # user picked in Kun since stays
            c.select(s, prev["provider"], prev["model"])
            s = c.snapshot()
        c.remove(s, mine["id"])
    except KunError as exc:
        raise _err(exc) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    """A model picked or dropped reaches the connection's list, and a gateway
    on a new port its address."""
    c = live_client(data_dir())
    if c is None:
        return kept
    try:
        s = c.snapshot()
        mine = ours(s, kept, at.base("openai-chat"))
        if mine is None:
            return kept
        m = mine.get("selectedModel") or ""
        if s.get("defaultProviderId") == mine["id"] and s.get("defaultModel"):
            m = s["defaultModel"]
        if moved(mine.get("baseUrl") or "", at.base("openai-chat")):
            return _wire(c, s, at, m, kept, keep=True)
        models = models_for(at, m)
        if models != list(mine.get("models") or []):
            c.patch_models(s, mine["id"], models)
    except KunError as exc:
        raise _err(exc) from exc
    return kept


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    s = read_registry(data_dir())
    if s is None:
        return []
    mine = ours(s, kept, at.base("openai-chat"))
    if mine is None or same_gateway(mine.get("baseUrl") or "", at.base("openai-chat")):
        return []
    return ["baseUrl"]


def _v1() -> str:
    from .engine import wiring
    try:
        return wiring(agent(), {}).base("openai-chat")
    except Exception:  # noqa: BLE001 - no gateway address known: by the kept id alone
        return ""


def _detect() -> bool:
    return data_dir().is_dir() or bool(app_bundles(APP))


def agent() -> Agent:
    return Agent(
        id="kun", name="Kun", icon="kun", kind="app", protocol="openai-chat",
        path=lambda: data_dir() / REGISTRY, dir=data_dir, detect_only=True, detect=_detect,
        fields=[Field("model", "Model")],
        wire=lambda at: [], live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
