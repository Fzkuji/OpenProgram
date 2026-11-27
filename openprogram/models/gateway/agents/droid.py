"""Factory's Droid CLI (``$FACTORY_HOME_OVERRIDE/.factory/settings.json``, default ``~/.factory``).

After yetone/magpie ``internal/agent/droid.go`` (MIT License, Copyright (c)
yetone). Models of the user's own (BYOK) are the ``customModels`` array, each
``{model, displayName, baseUrl, apiKey, provider, …}``, ``provider`` being
``anthropic`` (Messages), ``openai`` (Responses) or
``generic-chat-completion-api`` (Chat Completions); the older
``~/.factory/config.json`` ``custom_models`` is still read by droid, merged
under settings.json, and is left alone. A custom model is picked as
``custom:<id>``. The model sessions start on is
``sessionDefaultSettings.model``.

Connecting appends one entry per catalog model, id
``custom:openprogram/<provider>/<model>``, so the catalog joins droid's
``/model`` picker under Custom models, each on the API its provider speaks
natively, and sets ``sessionDefaultSettings.model``; the user's entries stay
in front, so their own ids don't move, and the default they had is stashed
and put back on disconnect.

droid judges a custom model by its ``model`` too: one whose name reads as
OpenAI's on another provider, or as a Claude off ``anthropic``, gets a
warning at every session start (:func:`wants`), so such a model is written
on the provider droid asks for and the gateway translates.

A field of one of OpenProgram's entries the user changed in settings.json
stays theirs (:data:`KEPT`, ``Members.kept``): what OpenProgram last gave
those fields is kept in the stash, and a value other than it is the user's. ``baseUrl`` and
``apiKey`` are the wiring, put back at every sync.

A model that reasons is given ``reasoningEffort``, the effort droid starts it
at: droid offers a custom model it doesn't know no thinking levels at all
unless its entry has one. One whose own id (the last part of the ref) is a
model droid has built in is given that id as ``baseModelId`` too, so droid
takes its levels and default from its own table (:data:`BUILTINS`).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write

MODELS = "customModels"
MODEL = "sessionDefaultSettings.model"
#: How every id OpenProgram gives a custom model begins.
ID_PREFIX = "custom:openprogram/"

#: The efforts droid's settings take for a custom model (droid 1.3.0's
#: reasoningEffort enum) that start it reasoning, in the order a default is
#: picked from when the model's list names none.
LEVELS = ("medium", "high", "low", "xhigh", "max", "minimal")

#: The fields of OpenProgram's entries a value the user set stays in.
#: baseUrl follows the provider; apiKey is the wiring.
KEPT = ("provider", "displayName", "maxContextLimit", "maxOutputTokens", "noImageSupport",
        "reasoningEffort", "baseModelId")

#: The models droid 1.3.0 has built in (its model id enum, the Factory
#: router's "auto" and "auto-fast" aside): the values baseModelId takes.
BUILTINS = frozenset("""
claude-3-5-sonnet-20241022 claude-3-7-sonnet-20250219 claude-sonnet-4-20250514 claude-opus-4-1-20250805
claude-3-5-haiku-20241022 claude-sonnet-4-5-20250929 claude-opus-4-5-20251101 claude-haiku-4-5-20251001
claude-sonnet-4-6 claude-opus-4-6 claude-opus-4-6-fast claude-opus-4-7 claude-opus-4-7-fast claude-opus-4-8
claude-opus-4-8-fast claude-opus-5 claude-opus-5-fast claude-opus-5-5 claude-opus-5-5-fast claude-sonnet-5
claude-sonnet-5-5 claude-haiku-5-5 claude-fable-5 claude-fable-5.1 atlas-07-21 aster-07-15 gpt-5-2025-08-07
gpt-5-mini-2025-08-07 gpt-5-nano-2025-08-07 gpt-5-codex gpt-5.1 gpt-5.1-codex gpt-5.1-codex-max gpt-5.2
gpt-5.2-codex gpt-5.3-codex gpt-5.3-codex-fast gpt-5.4 gpt-5.4-fast gpt-5.4-mini gpt-5.4-mini-fast gpt-5.5
gpt-5.5-fast gpt-5.5-pro gpt-5.6-sol gpt-5.6-sol-fast gpt-5.6-terra gpt-5.6-terra-flex gpt-5.6-luna
gpt-5.6-luna-flex gpt-6-astra gpt-6-astra-ultrafast gpt-6-astra-flex gpt-6.1-sol gpt-6.1-sol-ultrafast
gpt-6.1-terra gpt-6-sol gpt-6-sol-fast gpt-6-sol-flex gpt-6-luna gpt-6-luna-fast gpt-6-luna-flex gemini-2.5-flash
gemini-2.5-pro gemini-3-pro-preview gemini-3-flash-preview gemini-3.1-pro-preview gemini-3.5-flash gemini-3.6-flash
gemini-3.7-flash gemini-3.8-flash gemini-4-argon garnet-07-15 grok-4.5 grok-4.6 grok-4.7 glm-4.6 glm-4.7 kimi-k2.5
kimi-k2.6 kimi-k2.7-code kimi-k3 qwen3.8-max deepseek-v4.1-flash-1017 deepseek-v4.1-flash deepseek-v4-flash-0731
deepseek-v4-pro minimax-m2.5 minimax-m2.7 minimax-m3 minimax-m3.1-flash glm-5 glm-5.1 glm-5.2 glm-5.2-fast glm-5.3
glm-5.3-flash mistral-medium-3.5 mistral-large-4 inkling nemotron-3-ultra nemotron-3.5-super muse-spark-1.2
muse-spark-1.3 muse-spark-open beam-1.0
""".split())

# droid's own reading of a custom model's name (droid 0.231.0): one not on
# "openai" whose name matches _GPT is warned about as OpenAI's, then one on
# neither "anthropic" nor "bedrock-converse" matching _CLAUDE as Claude's.
_GPT = (re.compile(r"(?i)\bgpt[-_.]?\d"), re.compile(r"(?i)codex"), re.compile(r"(?i)\bo[1-9](?:[-_.]|$)"))
_CLAUDE = re.compile(r"(?i)claude|haiku|sonnet|opus")


def home() -> Path:
    return Path(os.environ.get("FACTORY_HOME_OVERRIDE") or str(Path.home())).expanduser() / ".factory"


def config_path() -> Path:
    return home() / "settings.json"


def wants(model: str) -> str:
    """The provider droid asks for a custom model by its name, or ``""``."""
    if any(pattern.search(model) for pattern in _GPT):
        return "openai"
    return "anthropic" if _CLAUDE.search(model) else ""


def builtin(ref: str) -> str:
    """The model of droid's own whose id is the last part of ``ref``, or ``""``."""
    name = ref.rsplit("/", 1)[-1].lower()
    return name if name in BUILTINS else ""


def effort(model: dict[str, Any]) -> str:
    """The reasoningEffort ``model`` starts at: its default level, else
    medium, else the nearest level it has; ``""`` for one that doesn't reason."""
    levels = list(model.get("thinking_levels") or [])
    if not model.get("reasoning") and not levels:
        return ""

    def has(level: str) -> bool:
        return not levels or level in levels
    default = model.get("default_thinking_level") or ""
    if default in LEVELS and has(default):
        return default
    return next((level for level in LEVELS if has(level)), "")


def _provider(model: dict[str, Any]) -> str:
    """The API the model's entry is on (magpie ``droidEntriesAt``): the one
    droid asks for by its name, else the API the model's provider speaks it
    on (the model row's ``api``), so the gateway relays it as it is:
    Responses on ``openai``, Messages on ``anthropic``, anything else on
    Chat Completions.

    magpie's routing groups (``provider.IsGroupRef``: no baseModelId, an
    effort only when the members share levels) have no counterpart here:
    each OpenProgram model is one provider's, so those branches are left out."""
    want = wants(model["id"])
    if want:
        return want
    api = (model.get("api") or "").lower()
    if "responses" in api:
        return "openai"
    if api == "anthropic-messages":
        return "anthropic"
    return "generic-chat-completion-api"


def _base(at: Wiring, provider: str) -> str:
    """The baseUrl droid sends a provider's requests to: the OpenAI SDKs join
    their path to /v1, Anthropic's adds the /v1 itself."""
    if provider == "anthropic":
        return at.base("anthropic")
    if provider == "openai":
        return at.base("openai-responses")
    return at.base("openai-chat")


def entry(at: Wiring, model: dict[str, Any]) -> dict[str, Any]:
    """One custom model, in droid's key order (magpie ``droidEntry``)."""
    provider = _provider(model)
    out: dict[str, Any] = {"model": model["id"], "id": ID_PREFIX + model["id"],
                           "displayName": model.get("name") or model["id"],
                           "baseUrl": _base(at, provider), "apiKey": at.key, "provider": provider}
    context, output = int(model.get("context_window") or 0), int(model.get("max_tokens") or 0)
    if context:
        out["maxContextLimit"] = context
    if output:
        out["maxOutputTokens"] = min(output, context) if context else output
    level = effort(model)
    if level:
        out["reasoningEffort"] = level
        if builtin(model["id"]):
            out["baseModelId"] = builtin(model["id"])
    # droid takes a model on Chat Completions as seeing no images unless told
    out["noImageSupport"] = not model.get("vision")
    return out


def _wire(at: Wiring) -> list[Write]:
    # A kept field whose value in settings.json isn't what OpenProgram last
    # wrote there is the user's and stays (magpie droidKept/droidMerge, the
    # engine's Members.kept); droid's own "index" goes, and baseUrl follows
    # the provider the entry ends up on.
    path = config_path()
    items = [entry(at, model) for model in at.models]

    def rebased(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "baseUrl": _base(at, str(item.get("provider") or ""))}
    return [
        Write(path, "json", listing=True,
              members={MODELS: Members({"id": {"prefix": ID_PREFIX}}, items, key="id", kept=KEPT,
                                       drop=("index",), derive=rebased)}),
        Write(path, "json", sets={MODEL: ID_PREFIX + at.model}),
    ]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)droid( |$)"):
        return "Droid reads its settings at start-up — restart open droid sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="droid", name="Droid", icon="factory", aliases=("factory", "factory-droid"), kind="cli",
        protocol="openai-chat", bin="droid", dir=home, path=config_path,
        # droid's requests to a custom model carry factory-cli/<version>
        ua=("factory-cli",), named_key=True, wire=_wire, notice=_notice,
    )
