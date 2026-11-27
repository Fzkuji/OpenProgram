"""Grok Build, xAI's grok CLI (``$GROK_HOME/config.toml``, default ``~/.grok``).

After yetone/magpie ``internal/agent/grok.go`` (MIT License, Copyright (c)
yetone). Grok Build keeps the model new sessions start with under
``[models] default``, and models of the user's own as ``[model."<id>"]``
tables. OpenProgram adds one such table per enabled model, named
``"openprogram/<provider>/<model>"``, pointing at the gateway with
OpenProgram's own key — a model with no key of its own would be sent the
user's xAI sign-in — so the catalog joins Grok's own models in its
``/model`` picker (``listing``: rewritten whenever the catalog changes, a
table for a model gone from it taken out).

A signed-in Grok also takes remote "campaign" patches from xAI, applied
above config.toml, and a launch campaign sets ``models.default`` (September
2026's grok-4.7-launch did): a default picked here was ignored and every new
session started on the campaign's model. So a default picked here turns
campaigns off (``[features] campaigns = false``), and disconnecting gives
them back. Grok's ``/model`` rewrites the default and its effort as one
pick: they go back on disconnect while the default still names one of
OpenProgram's, and a sync of the model list never writes them.

Each table's ``api_backend`` is the API the model's provider serves it on
natively, so the gateway relays what Grok sent as it is instead of
translating Chat Completions: Grok Build takes ``chat_completions`` (its
default), ``responses`` and ``messages`` (Anthropic Messages) and sends each
to ``base_url``'s ``/chat/completions``, ``/responses`` or ``/messages``. A
Messages model's ``base_url`` is the gateway's Anthropic ``/v1``, the others
its OpenAI ``/v1``.

Kigi (``kigi.py``), a fork of Grok Build, keeps the same config.toml in a
home of its own and is built here from its own :class:`GrokKind`.

The ``effort`` field sets ``[models] default_reasoning_effort``, the effort
new sessions start with; only words Grok knows are written (any other stops
it at start-up). Each table's ``auto_compact_threshold_percent`` is
OpenProgram's own ``context.auto_compact_percent``, so Grok compacts where
OpenProgram would; a percent the user set in a table, or one ``[session]``
sets for every model, stays theirs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Agent, Field, Pick, Wiring, Write
from .model_rows import PROVIDER_ID, choice, dotted, efforts, name, native_api, read_doc, window, wrote_before
from .model_rows import picked as picked_row

#: The reasoning efforts Grok knows, in its order (magpie ``grokEfforts``).
GROK_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class GrokKind:
    """An agent that keeps Grok Build's config.toml (magpie ``grokKind``):
    Grok Build itself, or a fork of it with a home and a name of its own."""

    id: str
    name: str
    aliases: tuple[str, ...]
    ua: tuple[str, ...]
    env: str        # the variable its home is moved to
    dir: str        # else this folder under the user's home
    proc: str       # the pattern of its running process
    notice: str     # what it says after a change while one runs
    icon: str = ""  # its mark; its id when empty


GROK_BUILD = GrokKind(
    id="grok", name="Grok Build", aliases=("grok-build", "grok-cli"), icon="xai",
    ua=("grok-shell", "grok-pager", "xai-grok-build"),  # grok-pager: its terminal front end
    env="GROK_HOME", dir=".grok", proc=r"(^|/)grok( |$)",
    notice="Grok Build reads its settings at start-up — restart open grok sessions to use this.",
)


def grok_backend(model: dict[str, Any]) -> str:
    """The ``api_backend`` a model is asked on (magpie ``grokBackend``): the
    API its provider serves it on natively; Chat, or not known, stays on Chat."""
    api = native_api(model)
    if api == "openai-responses":
        return "responses"
    if api == "anthropic-messages":
        return "messages"
    return "chat_completions"


def nearest_effort(effort: str, levels: list[str]) -> str:
    """The level of ``levels`` nearest ``effort`` in Grok's order, a tie going
    up (magpie ``catalog.NearestEffort``); ``""`` when none is comparable."""
    if effort not in GROK_EFFORTS:
        return ""
    at = GROK_EFFORTS.index(effort)
    best, gap = "", None
    for level in levels:
        if level not in GROK_EFFORTS:
            continue
        distance = GROK_EFFORTS.index(level) - at
        if gap is None or abs(distance) < abs(gap) or (abs(distance) == abs(gap) and distance > gap):
            best, gap = level, distance
    return best


def _home(kind: GrokKind) -> Path:
    moved = os.environ.get(kind.env, "").strip()
    return Path(moved).expanduser() if moved else Path.home() / kind.dir


def _table(ref: str) -> str:
    return dotted("model", f"{PROVIDER_ID}/{ref}")


def compact_percent(tokens: int, context: int) -> int:
    """The ``auto_compact_threshold_percent`` that has Grok compact at
    ``tokens`` of a ``context`` window (magpie ``grokPercent``): 0, Grok's
    own 85%, when it compacts at none of it; rounded down, so never later."""
    if tokens <= 0 or context <= 0 or tokens >= context:
        return 0
    return max(1, tokens * 100 // context)


def compact_tokens(model: dict[str, Any]) -> int:
    """Where OpenProgram compacts ``model``'s conversations: at
    ``context.auto_compact_percent`` of its window (magpie ``grokCompactAt``
    from its Long-conversations setting); 0 when the window isn't known."""
    context = window(model)
    if not context:
        return 0
    from openprogram.state.settings import setting_value
    try:
        percent = int(setting_value("context.auto_compact_percent"))
    except (TypeError, ValueError):
        return 0
    return context * percent // 100


def _compact_at(tables: dict[str, Any], table: str, model: dict[str, Any], context: int,
                had: dict[str, Any], before: dict[str, Any], session_percent: bool,
                drop: list[str]) -> None:
    """Set ``table``'s auto_compact_threshold_percent: one the user set there
    stays (magpie ``grokOwnPercent``: other than OpenProgram last wrote, or one
    OpenProgram never wrote); else OpenProgram's own, unless ``[session]``
    sets one for every model, which a per-model one would win over."""
    key = f"{table}.auto_compact_threshold_percent"
    own = had.get("auto_compact_threshold_percent")
    if isinstance(own, int) and not isinstance(own, bool) and 0 <= own <= 100 and before.get(key) != own:
        tables[key] = own
        return
    percent = compact_percent(compact_tokens(model), context)
    if percent and not session_percent:
        tables[key] = percent
    elif "auto_compact_threshold_percent" in had and key in before:
        drop.append(key)  # OpenProgram's, no longer given


def _original(agent_id: str, path: Path, key: str) -> Any:
    """The value ``key`` had before OpenProgram first wrote it; ``None`` when absent."""
    from .. import stash
    try:
        record = stash.entry(agent_id) or {}
    except stash.StashUnreadable:
        return None
    original = (((record.get("files") or {}).get(os.path.realpath(path)) or {}).get("originals") or {}).get(key) or {}
    return original.get("value") if original.get("had") else None


def _forget_stale(agent_id: str, path: Path, keys: list[str]) -> None:
    """Record a table of OpenProgram's own that a sync takes out as absent
    before OpenProgram: it was, and disconnecting must not bring it back."""
    from .. import stash
    try:
        stash.remember(agent_id, os.path.realpath(path), {key: (False, None) for key in keys}, fmt="toml")
    except stash.StashUnreadable:
        pass


def grok_like(kind: GrokKind) -> Agent:
    """The agent of ``kind`` (magpie ``grokLikeIn``), on this machine."""

    def home() -> Path:
        return _home(kind)

    def config_path() -> Path:
        return home() / "config.toml"

    def wire(at: Wiring) -> list[Write]:
        path = config_path()
        doc = read_doc("toml", path)
        had = doc.get("model") if isinstance(doc.get("model"), dict) else {}
        before = wrote_before(kind.id, path)
        session_percent = "auto_compact_threshold_percent" in (doc.get("session") or {})
        dropped: list[str] = []
        tables: dict[str, Any] = {}
        current = set()
        for model in at.models:
            ref = model["id"]
            current.add(f"{PROVIDER_ID}/{ref}")
            table = _table(ref)
            backend = grok_backend(model)
            base = at.base("anthropic") + "/v1" if backend == "messages" else at.base("openai-chat")
            tables[f"{table}.model"] = ref
            tables[f"{table}.name"] = name(model)
            tables[f"{table}.base_url"] = base
            tables[f"{table}.api_key"] = at.key
            tables[f"{table}.api_backend"] = backend
            context = window(model) or 0
            if context:
                # a context_window the user set by hand in OpenProgram's
                # table stays through a sync and a new pick (magpie
                # grokOwnWindow): one other than OpenProgram last gave it,
                # or, with no record, other than the model's window
                own = (had.get(f"{PROVIDER_ID}/{ref}") or {}).get("context_window")
                key = f"{table}.context_window"
                last = before.get(key)
                if isinstance(own, int) and own > 0 and own != context and own != last:
                    context = own
                tables[key] = context
            levels = [level for level in GROK_EFFORTS if level in efforts(model)]  # in Grok's order
            if levels:
                tables[f"{table}.reasoning_efforts"] = levels
            _compact_at(tables, table, model, context, had.get(f"{PROVIDER_ID}/{ref}") or {}, before,
                        session_percent, dropped)
        stale = [dotted("model", table) for table in had
                 if table.startswith(PROVIDER_ID + "/") and table not in current]
        if stale:
            _forget_stale(kind.id, path, stale)
        listing = Write(path, "toml", sets=tables, deletes=[*stale, *dropped], listing=True)
        if at.syncing:
            return [listing]  # the default is the user's to change in Grok

        sets: dict[str, Any] = {"models.default": f"{PROVIDER_ID}/{at.model}"}
        # the default model is the user's, not a campaign's
        if (doc.get("features") or {}).get("campaigns") is not False:
            sets["features.campaigns"] = False
        deletes: list[str] = []
        chosen = at.values.get("effort", "")
        picked = next((m for m in at.models if m["id"] == at.model), None)
        if chosen in GROK_EFFORTS:
            # the effort new sessions start with; Grok applies it to a model
            # that supports it and ignores it otherwise. Any other word stops
            # the agent at start-up ("invalid reasoning effort"), so none is written
            sets["models.default_reasoning_effort"] = chosen
        else:
            key = "models.default_reasoning_effort"
            now = (doc.get("models") or {}).get("default_reasoning_effort")
            effort = now
            if key in before and before[key] == now:
                # OpenProgram's (a pick since cleared, or a nearest one):
                # back to the effort the user had before OpenProgram, if any
                effort = _original(kind.id, path, key)
            # a model without the effort new sessions start on starts at its
            # nearest one (a tie goes up), not at Grok's default: switching to a
            # group of low/medium/high from xhigh keeps high (Creat.wu on Discord)
            levels = efforts(picked) if picked is not None else []
            if effort and levels and effort not in levels:
                effort = nearest_effort(effort, levels) or None
            if effort and effort != now:
                sets[key] = effort
            elif not effort and now is not None and effort != now:
                deletes.append(key)
        pick = Pick(tuple(key for key in ("models.default", "models.default_reasoning_effort") if key in sets),
                    "models.default", {"prefix": PROVIDER_ID + "/"})
        return [listing, Write(path, "toml", sets=sets, deletes=deletes, picks=(pick,))]

    def effort_choices(values: dict[str, str]) -> list[str]:
        """The picked model's efforts in Grok's order, else low, medium, high."""
        levels = [level for level in GROK_EFFORTS if level in efforts(picked_row(values))]
        return levels or ["low", "medium", "high"]

    def notice() -> str:
        from .running import any_running
        return kind.notice if any_running(kind.proc) else ""

    return Agent(
        id=kind.id, name=kind.name, icon=kind.icon or kind.id, aliases=kind.aliases, kind="cli",
        protocol="openai-chat", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("effort", "Effort", quiet=True, options=choice(kind.id, effort_choices))],
        ua=kind.ua, wire=wire, notice=notice,
    )


def agent() -> Agent:
    return grok_like(GROK_BUILD)
