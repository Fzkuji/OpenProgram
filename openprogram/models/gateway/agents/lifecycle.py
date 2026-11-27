"""Keeping connected agents in step with the providers and the Worker's port.

After yetone/magpie ``internal/agent/sync.go``, ``reseat.go``, ``rewire.go``
and ``mark.go`` (MIT License, Copyright (c) yetone):

* :func:`catalog_changed` runs in the Worker after every change to the
  enabled models (``providers/catalog/enabled_models.reload``, whose port
  only the Worker fills: a CLI command or a migration never rewrites an
  agent's files). Each connected agent that keeps a copy of the model list
  has it rewritten where it differs (magpie ``SyncCatalog``), and each field
  on a model no longer served moves to the same model from a provider still
  serving it (magpie ``Reseat``). A tier with none falls back to following
  the main model; a main model with none stays as it is and is reported,
  never disconnected: a provider turned off is no reason to take the agent
  off the gateway or clear its settings.
* :func:`rewire` runs as the Worker starts: an agent whose config names the
  gateway at another address than the profile's port now is written again
  (magpie ``Rewire``), its originals kept.

Each only touches an agent whose files still point at this instance as it
wrote them (``engine.owned``): the stable and the dev instance share
``~/.codex`` and ``~/.claude``, and the one that wired an agent last owns it.
* :func:`line_note` is the note over each TOML/YAML/dotenv line OpenProgram
  writes (``edit/mark.py``).
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .. import stash

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_run_lock = threading.Lock()  # one round of reseat and resync at a time
_last_catalog: Optional[frozenset[str]] = None
_DATED = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")

UNDO = "turning it off in OpenProgram undoes it"


def line_note(table: str, key: str, value: str) -> str:
    """The note a config line gets, ``""`` for one that isn't OpenProgram's (magpie ``lineNote``)."""
    from .codex import PROVIDER_ID
    if table == "" and key == "model_provider" and value == PROVIDER_ID:
        return f"Codex on OpenProgram's local gateway; {UNDO}, or delete this line and the model line"
    if table == "" and key == "model_catalog_json" and "openprogram" in value:
        return f"OpenProgram's model list for Codex; {UNDO}, or delete this line"
    if not on_gateway(value):
        return ""
    if table == f"model_providers.{PROVIDER_ID}":
        return "for Codex threads started on OpenProgram; unused while model_provider doesn't name openprogram"
    if table == "":
        return f"points at OpenProgram's local gateway; {UNDO}, or delete this line"
    return f"points at OpenProgram's local gateway; {UNDO}, or remove this entry and pick another model"


def on_gateway(value: str) -> bool:
    """``value`` is a URL on the gateway's port (on loopback)."""
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return False
    try:
        port = urlparse(value).port
    except ValueError:
        return False
    if not port:
        return False
    from ..address import worker_base_url
    try:
        return urlparse(worker_base_url(stable=True)).port == port and "/gateway" in value
    except Exception:  # noqa: BLE001 - no Worker port known
        return False


# Model keys (magpie reseat.go modelKey)

def model_key(model: str) -> str:
    """A model id as providers agree on it: its last part, lower case, dots and
    underscores as dashes, no date."""
    tail = model.rsplit("/", 1)[-1].lower().replace(".", "-").replace("_", "-")
    return _DATED.sub("", tail)


def same_model(ref: str, served: list[str]) -> str:
    """``ref``'s model from another provider still serving it: the same id,
    else one spelt differently (magpie ``sameModel``); ``""`` for none."""
    if "/" not in ref:
        return ""
    model = ref.split("/", 1)[1]
    want = model_key(model)
    alike = ""
    for other in served:
        other_model = other.split("/", 1)[1] if "/" in other else other
        if other_model == model:
            return other
        if not alike and model_key(other_model) == want:
            alike = other
    return alike


@dataclass
class Move:
    """One field moved off a model the gateway no longer serves (magpie ``Move``)."""

    agent: str
    field: str
    from_: str
    to: str          # "" for the agent's own default
    error: str = ""

    def __str__(self) -> str:
        if self.error:
            return f"{self.agent} {self.field}: still on {self.from_}, not moved: {self.error}"
        return f"{self.agent} {self.field}: {self.from_} → {self.to or 'its default'}"



def _connected() -> dict[str, dict[str, Any]]:
    """The agents this instance connected; none while the stash is unreadable."""
    try:
        return stash.connected()
    except stash.StashUnreadable as exc:
        _log.warning("gateway agents left alone: %s", exc)
        return {}


def reseat(served: list[str]) -> list[Move]:
    """Move every connected agent this instance owns off models not in
    ``served`` (magpie ``Reseat``): to the same model from a provider still
    serving it; a tier with none follows the main model again; a main model
    with none stays, reported with why it was not moved."""
    from . import find
    from .engine import AgentConfigError, connect, owned
    moves: list[Move] = []
    for agent_id, record in _connected().items():
        agent = find(agent_id)
        values = dict(record.get("values") or {})
        if agent is None or not agent.gateway or not values.get("model") or not owned(agent_id):
            continue
        changed = dict(values)
        field_moves = []
        # the last fields first: a tier moved after the main model would follow it
        for key in reversed(list(values)):
            ref = values[key]
            option = agent.field(key)
            if ref in served or (option is not None and option.options is not None
                                 and (not option.with_catalog or "/" not in ref)):
                continue  # a choice (Codex's "applies to"), or a model of the agent's own, is no model to move
            to = same_model(ref, served)
            if to:
                changed[key] = to
                field_moves.append(Move(agent.name, key, ref, to))
            elif key == "model":
                moves.append(Move(agent.name, key, ref, "", "no enabled model serves it; pick another"))
            else:
                changed.pop(key, None)
                field_moves.append(Move(agent.name, key, ref, ""))
        if not field_moves:
            continue
        try:
            connect(agent.id, changed, options=options_for(agent.id))
            _follow_settings(agent.id, changed)
        except AgentConfigError as exc:
            for move in field_moves:
                move.to, move.error = "", str(exc)
        moves.extend(reversed(field_moves))
    for move in moves:
        _log.info("reseat: %s", move)
    return moves


def _follow_settings(agent_id: str, values: dict[str, str]) -> None:
    """A harness agent's OpenProgram settings follow a reseat."""
    from . import find
    agent = find(agent_id)
    if agent is None or not agent.harness or not values.get("model"):
        return
    from openprogram.state.settings import set_setting
    set_setting(f"harnesses.{agent_id}.model", values["model"])
    if any(f.quiet for f in agent.fields):
        set_setting(f"harnesses.{agent_id}.tiers",
                    {f.key: values[f.key] for f in agent.fields if f.quiet and values.get(f.key)})


#: ``agent id → () -> options`` its wiring is given (Codex's prompt versions,
#: which the runtime reads). The runtime sits above the models layer, so it
#: hands them down here (``routes/settings/harness_models.py``); without one
#: an agent gets none, and what it wrote before stays recorded.
OPTIONS: dict[str, Callable[[], dict[str, Any]]] = {}


def provide_options(agent_id: str, source: Callable[[], dict[str, Any]]) -> None:
    OPTIONS[agent_id] = source


#: ``agent id → () -> [{"id", "label"}]``: the models the agent reaches on its
#: own, as the runtime reads them from the agent itself (DeepSeek Harness's,
#: from a session it opens), offered on its model field beside OpenProgram's
#: (``Field.with_catalog``). Handed down as :data:`OPTIONS` is; without one
#: the agent offers none of its own.
OWN_MODELS: dict[str, Callable[[], list[dict[str, Any]]]] = {}


def provide_own_models(agent_id: str, source: Callable[[], list[dict[str, Any]]]) -> None:
    OWN_MODELS[agent_id] = source


def own_models_for(agent_id: str) -> list[dict[str, Any]]:
    source = OWN_MODELS.get(agent_id)
    if source is None:
        return []
    try:
        return [dict(model) for model in source() or [] if isinstance(model, dict) and model.get("id")]
    except Exception:  # noqa: BLE001 - the agent's own models are read again next time
        _log.debug("%s's own models not read", agent_id, exc_info=True)
        return []


def options_for(agent_id: str) -> dict[str, Any]:
    source = OPTIONS.get(agent_id)
    if source is None:
        return {}
    try:
        return source()
    except Exception:  # noqa: BLE001 - offline: the catalog stays as it is
        return {}


def take_out() -> list[str]:
    """Take OpenProgram out of each app that was uninstalled when its agent was
    turned off, now that it answers again (magpie ``almaTakeOut``,
    ``shelleyTakeOut``); the ids of those done."""
    from .engine import take_out as take_out_one
    try:
        pending = stash.take_outs()
    except stash.StashUnreadable:
        return []
    return [agent_id for agent_id in pending if take_out_one(agent_id)]


def resync() -> list[str]:
    """Rewrite the model list of every owned connected agent where it differs; their ids."""
    from .engine import AgentConfigError, owned, sync
    take_out()
    synced = []
    for agent_id in _connected():
        try:
            if owned(agent_id) and sync(agent_id, options=options_for(agent_id)):
                synced.append(agent_id)
        except AgentConfigError as exc:
            _log.info("model list of %s not rewritten: %s", agent_id, exc)
    return synced


def catalog_changed() -> None:
    """The enabled models changed: resync and reseat connected agents, once
    per actual change, behind the caller on a thread the interpreter waits
    for, so a write is never cut off half way."""
    global _last_catalog
    try:
        if not _connected():
            return
        from ..conversion import enabled_model_refs
        served = frozenset(row["id"] for row in enabled_model_refs())
    except Exception:  # noqa: BLE001 - a registry mid-rebuild is read next time
        return
    with _lock:
        if served == _last_catalog:
            return
        _last_catalog = served

    def run() -> None:
        with _run_lock:
            try:
                if served:
                    # an empty list is a config that could not be read, never
                    # a reason to move every agent
                    reseat(sorted(served))
                resync()
            except Exception:  # noqa: BLE001 - logged, retried with the next change
                _log.exception("keeping agents in step with the model list failed")

    threading.Thread(target=run, daemon=False, name="gateway-agents-sync").start()


def _start_live(agent_id: str, record: dict[str, Any]) -> None:
    """An agent still at this address whose live wiring asks to be set again
    as the Worker starts (``Live.start``) has it set."""
    from . import find
    from .engine import AgentConfigError, wiring
    agent = find(agent_id)
    if agent is None or agent.live is None or agent.live.start is None:
        return
    try:
        agent.live.start(wiring(agent, dict(record.get("values") or {})), dict(record.get("live") or {}))
    except (AgentConfigError, OSError) as exc:
        _log.info("%s not set again at start: %s", agent.name, exc)


def rewire() -> list[str]:
    """Write the gateway's address again into owned agents that name another
    one, set again what a connected agent's live wiring keeps only until a
    restart, and take OpenProgram out of an app back after being uninstalled;
    the names of those moved."""
    from . import find
    from ..address import worker_base_url
    from .engine import AgentConfigError, connect, key_stale, owned
    now = worker_base_url(stable=True)
    moved = []
    take_out()
    for agent_id, record in _connected().items():
        if record.get("address") in (None, now):
            _start_live(agent_id, record)
        agent = find(agent_id)
        # a moved port, or a key of its own in place of the one it was given
        if record.get("address") in (None, now) and not (agent is not None and key_stale(agent, record)):
            continue
        if agent is None or not agent.gateway or not owned(agent_id):
            continue
        if agent.field("model") is not None and not (record.get("values") or {}).get("model"):
            continue
        try:
            connect(agent_id, dict(record["values"]), options=options_for(agent_id))
            moved.append(agent.name)
        except AgentConfigError as exc:
            _log.info("%s not moved to %s: %s", agent.name, now, exc)
    return moved


#: How often :func:`keep_running` looks (magpie ``scienceTick``).
KEEP_EVERY = 10.0


def keep_once() -> None:
    """One round of each connected agent's keeper (``Agent.keep``): Claude
    Science's daemon started again on the gateway, OpenMausBot's model list
    written again over the one it wrote back (magpie ``KeepClaudeScience``,
    ``KeepOpenMausBotListed``). Each keeper looks for itself whether what it
    keeps is still this instance's: what it keeps is what ``owned`` would
    call changed."""
    from . import find
    for agent_id in _connected():
        agent = find(agent_id)
        if agent is None or agent.keep is None:
            continue
        try:
            agent.keep()
        except Exception:  # noqa: BLE001 - logged, tried again next round
            _log.exception("keeping %s on the gateway failed", agent.name)


def keep_running(stop: threading.Event) -> None:
    """:func:`keep_once` every :data:`KEEP_EVERY` seconds until ``stop`` is set (the Worker)."""
    while not stop.wait(KEEP_EVERY):
        keep_once()
