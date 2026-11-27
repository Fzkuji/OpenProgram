"""The gateway's model list, with what clients read about each model.

Ported from yetone/magpie ``internal/gateway/gateway.go`` ``modelObject``,
``toolsTold``, ``model`` and ``info`` (MIT License, Copyright (c) yetone).
Each model is listed with its window under the names clients read it by
(``context_window``, ``context_length``, ``max_input_tokens``), its reply
limit, whether it reasons and at which levels (as objects and as a flat
list, for Catwalk-schema clients), its input modalities, and that it takes
tools (``tool_call``, ``supported_parameters``), which clients such as Cline
read before they offer a model their tools.
"""
from __future__ import annotations

from typing import Any, Optional


def model_object(row: dict[str, Any]) -> dict[str, Any]:
    levels = _levels(row)
    reasoning = bool(row.get("reasoning") or levels)
    entry: dict[str, Any] = {
        "id": row["id"], "object": "model", "type": "model", "created": 0,
        "created_at": "2025-01-01T00:00:00Z", "owned_by": row.get("provider") or row["id"].split("/", 1)[0],
        "display_name": row.get("name") or row["id"], "reasoning": reasoning,
        "supported_reasoning_levels": [{"effort": level} for level in levels],
        "reasoning_levels": levels,
    }
    window = int(row.get("context_window") or 0)
    if window > 0:
        entry["context_window"] = entry["context_length"] = entry["max_input_tokens"] = window
    output = int(row.get("max_tokens") or 0)
    if output > 0:
        entry["max_output_tokens"] = min(output, window) if window else output
    entry["modalities"] = {"input": ["text", "image"] if row.get("vision") else ["text"], "output": ["text"]}
    entry["tool_call"] = True
    entry["supported_parameters"] = ["tools", "tool_choice", *(["reasoning"] if reasoning else [])]
    return entry


#: The agent that reads :func:`cursor_local_model`'s fields from the list.
CURSOR_LOCAL = "cursor_local"
#: The gateway's API for each provider API it relays a model on, by the
#: names Cursor Private Inference reads in ``api_types``.
_API_TYPES = {"openai-completions": "chat_completions", "openai-responses": "responses",
              "azure-openai-responses": "responses", "anthropic-messages": "anthropic_messages"}


def _levels(row: dict[str, Any]) -> list[str]:
    return [str(level) for level in row.get("thinking_levels") or [] if level and level != "off"]


def cursor_local_model(entry: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """What Cursor Private Inference reads of a model, after magpie
    ``gateway.cursorLocalModel`` (MIT License, Copyright (c) yetone):
    ``api_types``, the APIs it may ask it on (the one its provider speaks,
    so a Claude served on Anthropic Messages is asked there and keeps its
    thinking; all three for one translated anyway), and ``capabilities``,
    without which it falls back to Chat and limits of its own. Its picker
    keeps a row's capabilities only when they say it streams, calls tools and
    writes text, and sends a Claude's level only on Anthropic Messages."""
    native = _API_TYPES.get(str(row.get("api") or ""))
    types = [native] if native else ["chat_completions", "responses", "anthropic_messages"]
    bare = str(row["id"]).rsplit("/", 1)[-1].lower()
    if bare.startswith("claude-") and "anthropic_messages" in types:
        types = ["anthropic_messages"]
    entry["api_types"] = types
    levels = _levels(row)
    caps: dict[str, Any] = {"supports_tool_use": True, "supports_streaming": True, "output_modalities": ["text"],
                            "supports_reasoning": bool(row.get("reasoning") or levels)}
    if levels:
        caps["reasoning_effort"] = levels
    window = int(row.get("context_window") or 0)
    if window > 0:
        caps["context_length"] = window
    output = int(row.get("max_tokens") or 0)
    if output > 0:
        caps["max_output_tokens"] = min(output, window) if window else output
    caps["supports_vision"] = bool(row.get("vision"))
    entry["capabilities"] = caps
    return entry


def models_list(agent: str = "") -> dict[str, Any]:
    """The models, each as :func:`model_object` gives it; for Cursor Private
    Inference (``agent``) with what it reads of each too."""
    from .conversion import enabled_model_refs
    data = []
    for row in enabled_model_refs():
        entry = model_object(row)
        if agent == CURSOR_LOCAL:
            cursor_local_model(entry, row)
        data.append(entry)
    out: dict[str, Any] = {"object": "list", "data": data, "has_more": False}
    if data:
        out["first_id"], out["last_id"] = data[0]["id"], data[-1]["id"]
    return out


#: The /models format Antigravity's app reads as its remote model catalog
#: (its settings' ``models.listSource``), after magpie
#: ``gateway/antigravity_models.go`` (MIT License, Copyright (c) yetone).
ANTIGRAVITY_FORMAT = "antigravity"
#: The app's language server keeps the first 32 entries of a catalog and
#: skips the rest ("exceeded the maximum placeholder limit (32)").
ANTIGRAVITY_MENU_MAX = 32
#: The levels the app's picker gathers a model's variants by, weakest
#: first, each as a name ends ("Gemini 3.1 Pro (High)").
ANTIGRAVITY_LEVEL_NAMES = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "XHigh", "max": "Max"}


def antigravity_levels(efforts: list[str]) -> list[str]:
    """The levels of ``efforts`` the app's picker offers, weakest first: none
    unless two or more (one is no choice)."""
    lowered = {str(e).lower() for e in efforts or []}
    found = [level for level in ANTIGRAVITY_LEVEL_NAMES if level in lowered]
    return found if len(found) >= 2 else []


def _antigravity_own(rows: list[dict[str, Any]], start: str) -> tuple[int, list[str]]:
    for index, row in enumerate(rows):
        if start and row["id"] == start:
            return index, antigravity_levels(_levels(row))
    return -1, []


def antigravity_fit(rows: list[dict[str, Any]], start: str) -> list[tuple[int, list[str]]]:
    """Which of ``rows`` the app's catalog lists, and at which levels, after
    magpie ``antigravityFit``: the first 32 models, each once, and each
    model's levels while the entries they add still fit. ``start``, the
    model the app starts on, is listed at its levels whatever else fits, and
    takes the last place when it is past the others."""
    s, own = _antigravity_own(rows, start)
    extra = max(len(own) - 1, 0)
    picks: list[list[Any]] = [[k, []] for k in range(min(len(rows), ANTIGRAVITY_MENU_MAX - extra))]
    if picks and s >= len(picks):
        picks[-1][0] = s
    room = ANTIGRAVITY_MENU_MAX - len(picks) - extra
    for pick in picks:
        if pick[0] == s:
            pick[1] = own
            continue
        levels = antigravity_levels(_levels(rows[pick[0]]))
        if levels and len(levels) - 1 <= room:
            pick[1] = levels
            room -= len(levels) - 1
    return [(k, levels) for k, levels in picks]


def _antigravity_start() -> str:
    try:
        from .agents.antigravity import start_model
        from .conversion import asked_at
        return asked_at(start_model())[0]
    except Exception:  # noqa: BLE001 - no config readable: list order alone
        return ""


def antigravity_model(ref: str, effort: str = "") -> str:
    """The id Antigravity's app is started on for ``ref``, after magpie
    ``AntigravityModel``: ``ref`` at ``effort`` (the effort picked for it)
    when the catalog lists it once a level and has that one, else at the
    level its picker itself starts it on, medium or its weakest; ``ref`` as
    it is otherwise."""
    from .conversion import enabled_model_refs
    rows = list(enabled_model_refs())
    for index, levels in antigravity_fit(rows, ref):
        if rows[index]["id"] == ref and levels:
            if effort in levels:
                return f"{ref}:{effort}"
            return f"{ref}:{'medium' if 'medium' in levels else levels[0]}"
    return ref


def _antigravity_entry(row: dict[str, Any], model_id: str, name: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"modelId": model_id, "displayName": name}
    window = int(row.get("context_window") or 0)
    if window > 0:
        entry["maxTokens"] = window
    output = int(row.get("max_tokens") or 0)
    if output > 0:
        entry["maxOutputTokens"] = min(output, window) if window else output
    if row.get("vision"):
        entry["supportsImages"] = True
    if row.get("reasoning") or _levels(row):
        entry["supportsThinking"] = True
    return entry


def antigravity_list(start: str = "") -> dict[str, Any]:
    """``{"list": [{modelId, displayName, maxTokens, …}]}`` as Antigravity's
    app (2.29) reads a remote catalog. Its gateway discovery reads a model's
    id alone and gives one it doesn't know a 200K window whatever the
    model's; this catalog is read first and takes a window per model. Its
    picker gathers the models whose names end "(Low)", "(Medium)", "(High)",
    "(XHigh)" or "(Max)" into one row with a menu of those levels, so a model
    with two or more of them is listed once a level, ``<id>:<level>`` named
    ``<name> (<Level>)``, and the gateway asks for the one picked at its
    level (``conversion.asked_at``). The app keeps 32 entries, so levels are
    listed only as far as they fit, the model it starts on (``start``, else
    the one its config names) first."""
    from .conversion import enabled_model_refs
    rows = list(enabled_model_refs())
    start = start or _antigravity_start()
    out = []
    for index, levels in antigravity_fit(rows, start):
        row = rows[index]
        name = row.get("name") or row["id"]
        if not levels:
            out.append(_antigravity_entry(row, row["id"], name))
        for level in levels:
            out.append(_antigravity_entry(row, f"{row['id']}:{level}", f"{name} ({ANTIGRAVITY_LEVEL_NAMES[level]})"))
    return {"list": out}


def find_model(model_id: str) -> Optional[dict[str, Any]]:
    """One listed model by id (a ``openprogram:`` prefix taken off), or ``None``."""
    from .conversion import REF_PREFIX, enabled_model_refs
    wanted = model_id[len(REF_PREFIX):] if model_id.startswith(REF_PREFIX) else model_id
    for row in enabled_model_refs():
        if row["id"] == wanted:
            return model_object(row)
    return None


def info() -> dict[str, Any]:
    """What ``GET /gateway/openai/v1`` answers: a client that lists a provider
    only when its base URL answers 2xx (Empryo) finds one here."""
    from .conversion import enabled_model_refs
    return {"name": "openprogram", "models": len(enabled_model_refs()),
            "apis": ["/v1/chat/completions", "/v1/responses", "/v1/responses/compact", "/v1/messages",
                     "/v1beta/models/{model}:generateContent", "/api/chat"]}


#: What Muse Code is told of a model whose window or reply size isn't known.
MUSE_CONTEXT = 128000
MUSE_OUTPUT = 32000


def muse_models() -> dict[str, Any]:
    """Muse Code's model list (magpie ``internal/gateway/muse.go`` ``museModels``).

    Muse asks for it at ``/muse-code/models`` on its endpoint's host, whatever
    path the endpoint has, before every session, and starts none when it isn't
    there; a model it lists is one whose ``metadata["muse-code"]`` says how
    Muse is to treat it (a row without it Muse hides, a list of none it
    refuses). The ids are the catalog's, which Muse sends back as the model
    it asks ``/v1/responses`` for. Muse hides a model whose limit lacks the
    window or the output, and without a limit asks for replies of 128K
    tokens: one not known is told 128K and Muse's own 32K."""
    from .conversion import enabled_model_refs
    data = []
    for row in enabled_model_refs():
        name = row.get("name") or row["id"]
        provider = row.get("provider") or row["id"].split("/", 1)[0]
        images = bool(row.get("vision"))
        levels = [level for level in row.get("thinking_levels") or [] if level and level != "off"]
        context = int(row.get("context_window") or 0) or MUSE_CONTEXT
        output = int(row.get("max_tokens") or 0)
        output = min(output, context) if output > 0 else min(MUSE_OUTPUT, context)
        meta = {"name": name, "family": provider, "is_hidden": False, "attachment": images,
                "reasoning": bool(levels or row.get("reasoning")), "temperature": False, "tool_call": True,
                "modalities": {"input": ["text", "image"] if images else ["text"], "output": ["text"]},
                "options": {"include": []}, "variants": {}, "description": f"{name} via OpenProgram",
                "limit": {"context": context, "output": output}}
        data.append({"id": row["id"], "object": "model", "created": 0, "owned_by": provider,
                     "metadata": {"muse-code": meta}})
    return {"object": "list", "data": data}
