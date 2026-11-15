"""@function decorator + runtime layer.

Single-format function definitions. Authors write:

    from openprogram.programs import function

    @function
    async def bash(command: str, timeout: int = 30) -> str:
        '''Run a shell command. Returns combined stdout/stderr/exit_code.

        Args:
            command: Shell command to execute.
            timeout: Max seconds before kill.
        '''
        ...

The decorator returns an ``AgentTool`` instance compatible with
``openprogram.runtime.agent.loop`` and registers it into a global
registry. Everything else (schema generation from type hints,
docstring parsing, char cap, persist-to-disk, sync→async, error
wrap, cancel/on_update injection, approval gating, caching) is
handled by this module so authors stay focused on business
logic.

Design synthesizes three external frameworks (see ``references/``):

  - Claude Code's ``Tool<Input, Output>`` contract — async
    ``execute(call_id, args, cancel, on_update) → AgentToolResult``,
    schema auto-derived from signature + docstring.
  - Hermes' toolset composition — ``TOOLSETS`` with ``includes``
    chain, recursive expansion with first-occurrence dedupe
    (see ``openprogram/programs/__init__.py``).
  - OpenClaw's per-channel policy — ``unsafe_in=[channel, ...]``
    drops the function from the tool list when the request came
    in on a blacklisted channel (see ``apply_tool_policy``).

Beyond the references this module adds three knobs none of them ship
(a result's share of a small model window is the agent loop's bound,
``spill.result_cap``, applied once where the result is stored):

  - **LLM-controllable timeout**: when the wrapped function declares
    a ``timeout`` parameter AND the decorator passed
    ``timeout_min`` / ``timeout_max``, the LLM-supplied value is
    clamped into that range and used both for ``asyncio.wait_for``
    and passed through to the function body.
  - **Streaming tail accumulator**: ``on_update(text)`` writes pipe
    through a bounded ring buffer. Multi-megabyte streaming output
    (long shell commands, browser console dumps) keeps a tail
    window rather than growing without bound.
  - **``can_use()`` pre-flight gate**: a no-arg callable checked
    once per dispatcher session before the function is offered to
    the LLM. Distinct from ``check_fn`` (env presence) and
    ``unsafe_in`` (channel blacklist); covers role-based gating
    where the session's user lacks the privilege for this function.

A result's cap is a weight (``state.store.session.weight``), a fixed estimate of
its tokens that is the same on every installation, not a tokenizer's
count.
"""
from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import inspect
import json
import re
import threading
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Union, get_args, get_origin

from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import TextContent
from openprogram.runtime.permissions.refusal import Refused

from .execution import (
    DEFAULT_STREAM_CAPACITY_CHARS,
    MIN_STREAM_CAPACITY_CHARS,
    ToolReturn,  # noqa: F401 -- @function authors import ToolReturn from here
    _normalize_result as _normalize_result_impl,
    _persist_full_result as _persist_full_result_impl,
    _tool_results_dir as _default_tool_results_dir,
    dispatch_sandbox_error,
    invoke_callable,
    timeout_tool_result,
)
import logging

_log = logging.getLogger(__name__)

# Re-export names tests and callers import from this module.
# ``_tool_results_dir`` stays a local function so monkeypatches on
# ``openprogram.programs._host.runtime._tool_results_dir`` still redirect persist.


def _tool_results_dir() -> Path:
    return _default_tool_results_dir()


def _persist_full_result(call_id: str, text: str) -> Path:
    return _persist_full_result_impl(call_id, text, fallback_dir=lambda: _tool_results_dir())


def _normalize_result(raw: Any, *, call_id: str, cap: int | None = None) -> AgentToolResult:
    return _normalize_result_impl(raw, call_id=call_id, cap=cap)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# The LLM tool_call_id of the tool currently executing, bound in
# ``_execute`` so tool bodies can correlate side-effects they emit with
# the block the UI drew for their call. ``task`` uses it to anchor its
# spawn card on the right execution-timeline row.
_current_tool_call_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_tool_call_id", default=None,
)

# A provider may reuse its wire ``tool_call_id`` in a later assistant turn.
# The dispatcher binds this qualified occurrence for DAG identity while the
# raw id remains available to tools that correlate provider messages.
_current_tool_call_occurrence_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_tool_call_occurrence_id", default=None,
)
_tool_call_identity_consumed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_tool_call_identity_consumed", default=False,
)


def current_tool_call_id() -> Optional[str]:
    """The tool_call_id of the in-flight tool call, or None outside one."""
    return _current_tool_call_id.get()


def current_tool_call_occurrence_id() -> Optional[str]:
    """The qualified occurrence id of the in-flight provider tool call."""
    return _current_tool_call_occurrence_id.get()


def tool_call_identity_consumed() -> bool:
    """Whether the current agentic wrapper already consumed its occurrence."""
    return bool(_tool_call_identity_consumed.get())


class Tier(str, Enum):
    """How the model sees a registered entry (program-model.md §5).

    ``RESIDENT``: its schema is in every tools array. ``DEFERRED``: only its
    name is listed; ``tool_search`` loads the schema when the model needs it.
    ``HIDDEN``: callable from Python, never shown to the model. Whether a
    call waits for the user's approval is the entry's approval policy
    (``requires_approval``), which applies on top of any tier.
    """

    RESIDENT = "resident"
    DEFERRED = "deferred"
    HIDDEN = "hidden"


def owner_kind(owner: str) -> str:
    """``tool``, ``plugin`` or ``application`` for a tool owner string.

    Built-in tools have no owner or ``tool:<name>``; ``plugin:<name>`` is a
    plugin; everything else (``application:<id>``, ``external:<package>``
    and a user package's bare name) is an application.
    """
    if not owner or owner.startswith("tool:"):
        return "tool"
    if owner.startswith("plugin:"):
        return "plugin"
    return "application"


def tool_owner(t: Any) -> str:
    """The owner of a registered entry: ``_program_owner`` recorded by its
    ProgramContext, else the package owner ``register_entries`` recorded."""
    return getattr(t, "_program_owner", None) or getattr(t, "_workflow_owner", None) or ""


def application_of(owner: str) -> str:
    """The application id an ``application:<id>`` owner names, else ``""``."""
    return owner[len("application:"):] if owner.startswith("application:") else ""


def tool_tier(t: Any) -> Tier:
    """The registered tier of ``t``.

    Tool-like objects built outside the registry carry only the ``_defer``
    flag; they read as deferred or resident from it.
    """
    tier = getattr(t, "_tier", None)
    if isinstance(tier, Tier):
        return tier
    return Tier.DEFERRED if getattr(t, "_defer", False) else Tier.RESIDENT


def set_tier(t: Any, tier: Tier | str) -> Any:
    """Give ``t`` its tier; ``_defer`` mirrors it for the dispatch readers.

    A registered tool also moves in or out of the hidden index.
    """
    tier = Tier(tier)
    setattr(t, "_tier", tier)
    setattr(t, "_defer", tier is Tier.DEFERRED)
    name = getattr(t, "name", None)
    with _registry_lock:
        if name is not None and _registry.get(name) is t:
            if tier is Tier.HIDDEN:
                _unexposed.add(name)
            else:
                _unexposed.discard(name)
    return t


def as_resident(t: Any) -> Any:
    """A resident copy of ``t`` for one request; the registered tool is unchanged."""
    if tool_tier(t) is not Tier.DEFERRED:
        return t
    from copy import copy
    promoted = copy(t)
    setattr(promoted, "_tier", Tier.RESIDENT)
    setattr(promoted, "_defer", False)
    return promoted


_registry: dict[str, AgentTool] = {}
_toolset_membership: dict[str, set[str]] = {}      # tool_name → set of toolsets
_unsafe_in_channel: dict[str, set[str]] = {}       # tool_name → set of unsafe-in channels
# The names whose tier is ``Tier.HIDDEN`` — Python-callable helpers that
# never appear in any LLM tools array. ``set_tier`` and ``register`` keep it.
_unexposed: set[str] = set()
# ponytail: one lock for register + iterate; split if watcher/request contention shows up
_registry_lock = threading.Lock()
_allowed_tool_names: contextvars.ContextVar[Optional[set[str]]] = contextvars.ContextVar(
    "_allowed_tool_names", default=None,
)


def register(tool: AgentTool, *, toolsets: list[str] = (),
             unsafe_in: list[str] = (), expose: bool = True) -> AgentTool:
    """Register a tool. Same name → overwrite (last import wins).

    `toolsets` lists the named groups this tool belongs to (e.g.
    ["core", "research"]). `unsafe_in` lists channel sources where
    the tool should be hidden by default (e.g. ["wechat"]). The tool's
    tier (``_tier``) is kept; `expose=False` makes it ``Tier.HIDDEN``:
    out of every LLM tools array, still Python-callable and in the
    registry.
    """
    _departed.pop(getattr(tool, "name", None), None)
    if not expose:
        setattr(tool, "_tier", Tier.HIDDEN)
    elif not isinstance(getattr(tool, "_tier", None), Tier):
        setattr(tool, "_tier", tool_tier(tool))
    setattr(tool, "_defer", tool._tier is Tier.DEFERRED)
    with _registry_lock:
        _registry[tool.name] = tool
        if toolsets:
            _toolset_membership.setdefault(tool.name, set()).update(toolsets)
        if unsafe_in:
            _unsafe_in_channel.setdefault(tool.name, set()).update(unsafe_in)
        if tool._tier is Tier.HIDDEN:
            _unexposed.add(tool.name)
        else:
            _unexposed.discard(tool.name)   # re-register as exposed clears prior opt-out
    return tool


def unregister(name: str, *, reason: str = "") -> None:
    """Remove a tool, as when a workflow package is reloaded or deleted.

    A conversation that loaded it keeps its definition and is told why a
    call of it fails (``unavailable_tool``); ``reason`` says why."""
    if reason:
        mark_unavailable([name], reason)
    with _registry_lock:
        _registry.pop(name, None)
        _toolset_membership.pop(name, None)
        _unsafe_in_channel.pop(name, None)
        _unexposed.discard(name)


def get(name: str) -> Optional[AgentTool]:
    with _registry_lock:
        return _registry.get(name)


def current_tool(tool: AgentTool) -> AgentTool:
    """``tool`` as it describes itself now: a copy with its ``describe`` hook applied, or ``tool`` itself."""
    describe = getattr(tool, "_describe", None)
    if describe is None:
        return tool
    try:
        fresh = describe() or {}
    except Exception:
        # A failing hook leaves the registered description, which names everything.
        _log.debug("describe hook failed for %s", tool.name, exc_info=True)
        return tool
    if not fresh:
        return tool
    copied = copy.copy(tool)
    if "description" in fresh:
        copied.description = fresh["description"]
    if "parameters" in fresh:
        copied.parameters = fresh["parameters"]
    return copied


def all_tools() -> list[AgentTool]:
    with _registry_lock:
        return list(_registry.values())


def exposed_names() -> set[str]:
    """Every registered tool name whose tier is not hidden: the universe
    every preset and the per-call cascade filter subsets of, so an MCP
    tool or a newly loaded application entry is visible on registration."""
    with _registry_lock:
        return set(_registry.keys()) - _unexposed


def filter_for(*, names: Optional[list[str]] = None,
               toolset: Optional[str] = None,
               source: Optional[str] = None) -> list[AgentTool]:
    """Pick tools by name list, toolset name, or both. Excludes any
    tool flagged unsafe in `source`.
    """
    with _registry_lock:
        if names is not None:
            candidates = [t for t in (_registry.get(n) for n in names) if t is not None]
        elif toolset is not None:
            candidates = [t for t in _registry.values()
                          if toolset in _toolset_membership.get(t.name, ())]
        else:
            candidates = list(_registry.values())
        if source:
            candidates = [t for t in candidates
                          if source not in _unsafe_in_channel.get(t.name, ())]
        return candidates


def reset_registry() -> None:
    """Test-only — wipe registered tools so test imports are repeatable."""
    with _registry_lock:
        _registry.clear()
        _toolset_membership.clear()
        _unexposed.clear()
        _unsafe_in_channel.clear()
    _allowed_tool_names.set(None)
    _turn_resident_names.set((None, frozenset()))


def snapshot_registry() -> dict:
    """Test-only — capture the full registry state (deep copy) so it can be
    restored later via :func:`restore_registry`. Use this instead of a bare
    :func:`reset_registry` in teardown: reset_registry() empties the shared
    process-wide registry, and since the real tool modules are already
    imported (cached in sys.modules) their ``@function`` decorators won't
    re-fire — leaving every *later*-running test to see an empty registry.
    Snapshot before, restore after, and the isolation stays local."""
    with _registry_lock:
        return {
            "registry": dict(_registry),
            "toolset_membership": {k: set(v) for k, v in _toolset_membership.items()},
            "unsafe_in_channel": {k: set(v) for k, v in _unsafe_in_channel.items()},
            "unexposed": set(_unexposed),
        }


def restore_registry(snapshot: dict) -> None:
    """Test-only — put the registry back to a :func:`snapshot_registry` state."""
    with _registry_lock:
        _registry.clear()
        _registry.update(snapshot["registry"])
        _toolset_membership.clear()
        _toolset_membership.update(
            {k: set(v) for k, v in snapshot["toolset_membership"].items()})
        _unsafe_in_channel.clear()
        _unsafe_in_channel.update(
            {k: set(v) for k, v in snapshot["unsafe_in_channel"].items()})
        _unexposed.clear()
        _unexposed.update(snapshot["unexposed"])


# ---------------------------------------------------------------------------
# Schema generation from type hints + docstring
# ---------------------------------------------------------------------------

_DOC_ARG_RE = re.compile(r"^\s*(\w+)\s*:\s*(.+)$")

def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Returns (description, {arg_name: arg_doc}).

    Description = first paragraph. Arg docs from a Google-style
    "Args:" section. Other sections (Returns, Raises) ignored.
    """
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).split("\n")
    desc_lines: list[str] = []
    args: dict[str, str] = {}
    in_args = False
    desc_done = False  # flips after first blank line — preserves rest
    current_arg: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            current_arg = None
            desc_done = True
            continue
        if in_args and stripped.lower() in ("returns:", "return:", "raises:",
                                              "yields:", "examples:"):
            in_args = False
            current_arg = None
            continue
        if in_args:
            m = _DOC_ARG_RE.match(line)
            if m:
                current_arg = m.group(1)
                args[current_arg] = m.group(2).strip()
            elif current_arg and stripped:
                args[current_arg] += " " + stripped
            continue
        if desc_done:
            continue
        if stripped:
            desc_lines.append(stripped)
        elif desc_lines:
            # First blank line ends the short-description paragraph
            # but we KEEP scanning (Args: may come later).
            desc_done = True
    return " ".join(desc_lines).strip(), args


_PRIMITIVE_TYPES = {
    str: "string", int: "integer", float: "number", bool: "boolean",
}


def _python_type_to_json_schema(tp: Any, *, preserve_null: bool = False) -> dict[str, Any]:
    """Best-effort conversion. Handles primitives, Optional, list[X],
    dict, Literal[...], Union[A, B] (becomes {"oneOf": [...]}).
    Mapping values preserve declared null unions recursively. Ordinary fields
    retain the existing Optional/default conversion. Anything exotic falls
    back to {} (LLM gets a free-form value)."""
    if tp is None or tp is type(None):
        return {"type": "null"}
    if tp in _PRIMITIVE_TYPES:
        return {"type": _PRIMITIVE_TYPES[tp]}

    origin = get_origin(tp)
    args = get_args(tp)

    # Two union spellings reach here and they have DIFFERENT origins:
    #   * ``Optional[X]`` / ``Union[X, None]``  → origin is ``typing.Union``
    #   * ``X | None`` (PEP 604, py3.10+)        → origin is ``types.UnionType``
    # The old code only matched ``typing.Union``, so every parameter
    # written in the modern ``X | None`` style (e.g. ``path: str | None``)
    # fell through to the ``return {}`` at the bottom and produced a
    # schema with NO ``type`` key. OpenAI/codex's Responses API rejects
    # such a tool with HTTP 400 ("parameter '<x>' must have a 'type'
    # key"), which silently broke tool use for every tool using that
    # syntax (bash.timeout, glob.path, …) — including the main chat.
    import types as _types
    _union_origins = (Union, getattr(_types, "UnionType", ()))
    if origin in _union_origins:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            # Optional[X] → schema of X (caller marks it optional via
            # absence from required list).
            value = _python_type_to_json_schema(non_none[0], preserve_null=preserve_null)
        else:
            value = {"oneOf": [_python_type_to_json_schema(a, preserve_null=preserve_null) for a in non_none]}
        return _allow_null(value) if preserve_null and type(None) in args else value

    if origin in (list, tuple):
        if args:
            return {"type": "array", "items": _python_type_to_json_schema(args[0], preserve_null=preserve_null)}
        return {"type": "array"}

    from collections.abc import Mapping
    if tp in (dict, Mapping) or origin in (dict, Mapping):
        values = _python_type_to_json_schema(args[1], preserve_null=True) if len(args) > 1 else True
        return {"type": "object", "additionalProperties": values}

    # Literal[...]
    if hasattr(tp, "__class__") and tp.__class__.__name__ == "_LiteralGenericAlias":
        return {"enum": list(args)}

    return {}


def _allow_null(schema: dict[str, Any]) -> dict[str, Any]:
    """Widen a JSON schema so it also accepts ``null``. Used for optional
    params (those with a Python default) — the model may pass null to mean
    "unspecified", and the impl already handles None/""; rejecting null at
    the validator would make the whole tool call fail. Idempotent."""
    if not schema:
        # Bare {} already accepts anything (incl. null) — nothing to do.
        return schema
    out = dict(schema)
    t = out.get("type")
    if t is None:
        if "oneOf" in out:
            variants = list(out["oneOf"])
            if not any(v.get("type") == "null" for v in variants):
                variants.append({"type": "null"})
            out["oneOf"] = variants
        elif "enum" in out:
            if None not in out["enum"]:
                out["enum"] = [*out["enum"], None]
        # else: no type/oneOf/enum — treat as free-form, accepts null.
        return out
    if (t == "null" or (isinstance(t, list) and "null" in t)):
        return out
    if _holds_object(out):
        # The Codex backend fails the whole request, with an empty error event,
        # when a type list with "null" describes an object or an array of
        # objects; the same choice spelled as anyOf is accepted.
        description = out.pop("description", None)
        widened: dict[str, Any] = {"anyOf": [out, {"type": "null"}]}
        if description is not None:
            widened["description"] = description
        return widened
    out["type"] = [*t, "null"] if isinstance(t, list) else [t, "null"]
    return out


def _holds_object(schema: dict[str, Any]) -> bool:
    """An object with declared fields, or an array of such objects; free-form
    mappings are accepted as a type list and keep that form."""
    def declared(node: dict[str, Any]) -> bool:
        return _has_object_type(node) and ("properties" in node or node.get("additionalProperties") is False)
    if declared(schema):
        return True
    t = schema.get("type")
    items = schema.get("items")
    return (t == "array" or (isinstance(t, list) and "array" in t)) and isinstance(items, dict) and declared(items)


def _has_object_type(schema: dict[str, Any]) -> bool:
    t = schema.get("type")
    return t == "object" or (isinstance(t, list) and "object" in t)


def _close_objects(schema: Any) -> Any:
    """Close declared object fields while retaining dynamic mapping schemas."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _close_objects(v) for k, v in out["properties"].items()}
    if isinstance(out.get("items"), dict):
        out["items"] = _close_objects(out["items"])
    if isinstance(out.get("additionalProperties"), dict):
        out["additionalProperties"] = _close_objects(out["additionalProperties"])
    for key in ("oneOf", "anyOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [_close_objects(v) for v in out[key]]
    if _has_object_type(out) and "additionalProperties" not in out:
        out["additionalProperties"] = "properties" not in out
    return out


def _widen_optionals_to_null(params: Any) -> Any:
    """Given a tool's top-level ``parameters`` object schema, widen every
    OPTIONAL property (one not listed in ``required``) so it also accepts
    ``null``. Close nested declared-field objects while preserving anonymous
    and typed mappings, which provider adapters send without strict mode.

    One central place, applied to both generated and hand-written schemas.
    Idempotent; leaves non-object / malformed schemas untouched."""
    if not isinstance(params, dict):
        return params
    props = params.get("properties")
    if not isinstance(props, dict):
        return params
    required = set(params.get("required") or [])
    out = dict(params)
    out["properties"] = {
        name: _close_objects(
            sch if name in required or not isinstance(sch, dict)
            else _allow_null(sch))
        for name, sch in props.items()
    }
    return out


def _null_defaults(fn: Callable, sig: inspect.Signature) -> dict[str, Any]:
    """Optional parameters whose annotation excludes None, with their defaults."""
    import types
    import typing
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    out: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty or param.default is None:
            continue
        hint = hints.get(name, Any)
        if hint is Any or hint is type(None):
            continue
        if get_origin(hint) in (Union, types.UnionType) and type(None) in get_args(hint):
            continue
        out[name] = param.default
    return out


def _build_parameters_schema(fn: Callable) -> dict[str, Any]:
    """Inspect fn's signature + docstring → JSON schema for `parameters`.

    Uses ``typing.get_type_hints`` so string annotations from
    ``from __future__ import annotations`` resolve to real types.
    Falls back gracefully when a hint references something the
    runtime can't resolve (returns {} for that arg's schema).
    """
    import typing
    sig = inspect.signature(fn)
    _, arg_docs = _parse_docstring(fn.__doc__ or "")
    try:
        resolved_hints = typing.get_type_hints(fn)
    except Exception:
        resolved_hints = {}

    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        # Framework-injected kwargs — never exposed to the LLM
        if name in {"on_update", "cancel", "ctx", "context"}:
            continue
        # *args / **kwargs unsupported
        if param.kind in (inspect.Parameter.VAR_POSITIONAL,
                           inspect.Parameter.VAR_KEYWORD):
            continue

        ann = resolved_hints.get(name)
        if ann is None and param.annotation is not inspect.Parameter.empty:
            ann = param.annotation  # last-resort raw annotation
        schema = _python_type_to_json_schema(ann) if ann is not None else {}
        if name in arg_docs:
            schema["description"] = arg_docs[name]
        properties[name] = schema
        # A param with a Python default is optional → not in ``required``.
        # (Widening optionals to accept null happens once, centrally, at the
        # schema exit point in ``function()`` — see _widen_optionals_to_null.)
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        **({"required": required} if required else {}),
    }


# ---------------------------------------------------------------------------
# Approval gate evaluator
# ---------------------------------------------------------------------------

def _evaluate_approval(
    requires_approval: Union[bool, Callable[..., Any], None],
    args: dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Returns (needs_approval, reason).

    - True → always require approval (reason=None)
    - callable → invoke with **args; bool result, or string reason
      (truthy str = require, return the reason for the UI prompt)
    """
    if requires_approval is None or requires_approval is False:
        return False, None
    if requires_approval is True:
        return True, None
    try:
        verdict = requires_approval(**args)
    except Exception:
        # Conservative: if the gate function blows up, require approval
        return True, "approval gate raised; defaulting to require"
    if verdict is True:
        return True, None
    if verdict is False or verdict is None:
        return False, None
    if isinstance(verdict, str):
        return True, verdict
    return bool(verdict), None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: AgentToolResult
    expires_at: float


_cache: dict[str, _CacheEntry] = {}


def _cache_key(name: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"name": name, "args": args},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_get(key: str) -> Optional[AgentToolResult]:
    e = _cache.get(key)
    if e is None:
        return None
    if e.expires_at < time.time():
        _cache.pop(key, None)
        return None
    return e.value


def _cache_set(key: str, value: AgentToolResult, ttl: float) -> None:
    _cache[key] = _CacheEntry(value=value, expires_at=time.time() + ttl)


# ---------------------------------------------------------------------------
# Streaming tail accumulator
# ---------------------------------------------------------------------------
#
# Long-running tools emit progress through ``on_update(text)``. Without
# a bound, megabyte-scale streams (a noisy shell command, a browser
# console dump) grow without limit in memory. The accumulator keeps
# at most ``capacity`` characters from the *tail*; head bytes drop
# when capacity overflows. Modelled on claude-code's
# ``EndTruncatingAccumulator`` pattern.

class _TailAccumulator:
    """Bounded ring buffer for streamed progress text.

    ``push(text)`` is O(1) amortised; head bytes are evicted lazily
    when total exceeds capacity. ``dump()`` returns the current
    tail, prefixed with a ``[…N chars dropped…]`` marker when content
    has been evicted.
    """

    __slots__ = ("_capacity", "_buf", "_total", "_dropped")

    def __init__(self, capacity: int) -> None:
        self._capacity = max(MIN_STREAM_CAPACITY_CHARS, capacity)
        self._buf: list[str] = []
        self._total: int = 0
        self._dropped: int = 0

    def push(self, text: str) -> None:
        if not text:
            return
        self._buf.append(text)
        self._total += len(text)
        if self._total <= self._capacity:
            return
        # Evict from the head until under capacity. Amortised O(1):
        # each character is dropped at most once.
        excess = self._total - self._capacity
        i = 0
        while i < len(self._buf) and excess > 0:
            seg = self._buf[i]
            if len(seg) <= excess:
                excess -= len(seg)
                self._dropped += len(seg)
                self._total -= len(seg)
                i += 1
            else:
                self._buf[i] = seg[excess:]
                self._dropped += excess
                self._total -= excess
                excess = 0
        if i:
            del self._buf[:i]

    def dump(self) -> str:
        body = "".join(self._buf)
        if self._dropped <= 0:
            return body
        return f"[…{self._dropped:,} chars dropped from head…]\n{body}"


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------

def function(
    fn: Optional[Callable] = None,
    *,
    # Model-facing surface (Claude Code Tool contract)
    name: Optional[str] = None,
    description: Optional[str] = None,
    label: Optional[str] = None,
    parameters: Optional[dict[str, Any]] = None,
    # Result handling
    max_result_weight: Optional[int] = None,
    stream_capacity_chars: Optional[int] = None,
    # Time
    timeout: Optional[float] = None,
    timeout_min: Optional[float] = None,
    timeout_max: Optional[float] = None,
    # Cache
    cache: bool = False,
    cache_ttl: float = 300.0,
    # Gating (declarative; reads consolidated in ``is_available_agent_tool``)
    check_fn: Optional[Callable[[], bool]] = None,
    requires_env: tuple = (),
    can_use: Optional[Callable[[], bool]] = None,
    describe: Optional[Callable[[], dict[str, Any]]] = None,
    requires_approval: Union[bool, Callable[..., Any], None] = None,
    accept_edits_safe: bool = False,   # acceptEdits 档下自动放行（写安全工具）
    # Selection metadata
    toolset: list[str] = (),               # Hermes — TOOLSETS membership
    unsafe_in: list[str] = (),              # OpenClaw — channel blacklist
    expose: bool = True,                    # Layer 2 — False hides from LLM
    # Layer 1 (Claude Code "conditional import") + Layer 6 ("deferred")
    available_if: Optional[Callable[[], bool]] = None,
    defer: bool = False,
    register_globally: bool = True,
    # Dispatch-layer sandbox / URL fail-closed. None = infer from
    # path/file_path + tool name. {} / [] = explicit exemption.
    path_params: Optional[dict[str, str]] = None,
    url_params: Optional[list[str]] = None,
):
    """Wrap a plain function as a registered AgentTool.

    Author writes a normal sync/async Python function with type hints
    and a Google-style docstring. The decorator extracts:

      - name: from override or ``fn.__name__``
      - description: from override or first docstring paragraph
      - parameters JSON schema: from override or fn signature +
        docstring "Args:" section

    Runtime extras (declarative kwargs):

      max_result_weight: a smaller bound than the model's for this
        tool's results, as a weight (``state.store.session.weight``: a CJK character
        counts 1, anything else 0.25). The tool itself never cuts: the
        agent loop bounds every result once, to the smaller of this and
        ``spill.result_cap()``, saving the whole text to the
        conversation's ``tool_results/<sha256>.txt`` (the profile's
        ``tool_results/`` outside a conversation) and naming it in the
        result. A larger value has no effect.
      stream_capacity_chars: bound on the streaming on_update tail
        accumulator. Defaults to ``DEFAULT_STREAM_CAPACITY_CHARS``.

      timeout: hard kill after N seconds (``asyncio.wait_for``).
      timeout_min / timeout_max: when set AND the wrapped function
        declares a ``timeout`` parameter, the LLM-passed value is
        clamped into ``[timeout_min, timeout_max]`` and used as both
        the framework's wait_for budget and the function-body value.

      cache + cache_ttl: memoize results keyed on (name, args).

      check_fn: no-arg callable; returns False to mark the function
        unavailable (e.g. missing optional dep). Distinct from
        ``can_use`` — check_fn is process-level, can_use is session-
        level.
      requires_env: env-var names that must be set for the function
        to be available (e.g. ``("OPENAI_API_KEY",)``).
      can_use: no-arg session-level pre-flight. Returns False to hide
        the function from this session's tool list. Modelled on
        Claude Code's role/policy gating; distinct from ``check_fn``
        (env presence) and ``unsafe_in`` (channel blacklist).
      describe: no-arg callable returning ``description`` and/or
        ``parameters`` as they stand now. The tool list is resolved
        with them each time (``current_tool``), so a tool names only
        what this computer offers at the moment: a target that is not
        configured, or a browser without its extension, is left out
        instead of described and then refused.
      requires_approval: True | False | callable(**args) -> bool|str.
        Read by the dispatcher's approval wrapper.

      toolset / unsafe_in: registry-side metadata. Toolset places this
        function in named presets (research, browser, …); unsafe_in
        drops it when the request came in on a blacklisted channel.

      available_if: no-arg callable evaluated *once at decoration time*.
        Returns False → registration is skipped entirely; the function
        does not enter ``_registry`` and is unreachable for the rest
        of the process. This is the equivalent of Claude Code's
        layer 1 (conditional ``require()`` based on build flag or
        ``USER_TYPE``). Use it for features that should be absent
        from a build, not just hidden — e.g. enterprise-only tools
        in an open build, or test-only fixtures in production.
        Distinct from ``check_fn`` (queried every call) and ``can_use``
        (queried every session); ``available_if`` runs once and the
        decision is permanent for this process.
      defer: when True, the function is registered but its full
        parameters schema is NOT shipped to the LLM in the default
        tools array. Instead it appears in a "deferred catalog"
        passed through the system prompt; the LLM has to call
        ``tool_search(select="<name>,...")`` to bring the schema
        into the next provider request's tools array. Matches Claude Code's
        ``shouldDefer`` flag + ToolSearch flow. Use this for tools
        whose schemas are large (MCP tools, niche helpers) so the
        common-path prompt stays cheap; the LLM still discovers
        them by name from the catalog listing.

      path_params: map of arg name → ``"read"`` / ``"write"``. Checked
        against ``validate_read_path`` / ``validate_write_path`` before
        the body runs. ``None`` falls back to ``path`` / ``file_path``
        plus the tool name (unknown direction → write). ``{}`` exempts.
      url_params: arg names checked with ``normalize_url`` (http/https
        only). ``None`` / ``[]`` means no URL check at dispatch.

    Framework injects three optional kwargs into the wrapped fn if it
    declares them in its signature:

      cancel:    asyncio.Event — set when the user aborts.
      on_update: callable(text) — write a progress line; routed through
        a bounded tail accumulator so unbounded streams don't OOM.
      timeout:   if the LLM passed a value AND timeout_min/timeout_max
        are configured, the framework clamps it and forwards the
        clamped value here.
    """
    if fn is None:
        def _inner(f):
            return function(
                f, name=name, description=description, label=label,
                parameters=parameters,
                max_result_weight=max_result_weight,
                stream_capacity_chars=stream_capacity_chars,
                timeout=timeout, timeout_min=timeout_min,
                timeout_max=timeout_max,
                cache=cache, cache_ttl=cache_ttl,
                check_fn=check_fn, requires_env=requires_env,
                can_use=can_use, describe=describe,
                requires_approval=requires_approval,
                accept_edits_safe=accept_edits_safe,
                toolset=toolset, unsafe_in=unsafe_in, expose=expose,
                available_if=available_if, defer=defer,
                register_globally=register_globally,
                path_params=path_params, url_params=url_params,
            )
        return _inner

    # Layer 1 — conditional import / registration.
    # Evaluated once, here, at decoration time. If the predicate is
    # set and returns falsy (or raises), we short-circuit the entire
    # decorator: no AgentTool is built, no entry lands in _registry,
    # and ``get(name)`` will return None forever in this process.
    # The undecorated function is returned so any module-level
    # ``some_fn = function(...)(impl)`` callers don't get None.
    if available_if is not None:
        try:
            if not available_if():
                return fn
        except Exception:
            return fn

    actual_name = name or fn.__name__
    sig = inspect.signature(fn)
    doc_desc, _ = _parse_docstring(fn.__doc__ or "")
    actual_description = description or doc_desc or fn.__name__
    # Single exit point for a tool's parameter schema — whether hand-written
    # (``parameters=``) or generated from the signature. Widen every optional
    # param (one not in ``required``) to also accept null, so the model can
    # pass ``null`` for "unspecified" without the validator rejecting the whole
    # call. This matches the OpenAI strict / Structured-Outputs convention
    # ("all fields required, optionality via a null type") and covers ALL
    # tools uniformly — including the ~80 hand-written-schema params that the
    # generator path never touched.
    actual_parameters = _widen_optionals_to_null(
        parameters or _build_parameters_schema(fn))
    null_defaults = _null_defaults(fn, sig)
    is_async_fn = inspect.iscoroutinefunction(fn)
    accepts_cancel = "cancel" in sig.parameters
    accepts_on_update = "on_update" in sig.parameters
    accepts_timeout = "timeout" in sig.parameters
    timeout_is_clampable = (
        accepts_timeout
        and (timeout_min is not None or timeout_max is not None)
    )

    async def _execute_bound(call_id: str,
                              args: dict[str, Any],
                              cancel_event,        # asyncio.Event | None
                              on_update_cb) -> AgentToolResult:  # callable | None
        # The public wrapper below binds the id for the complete operation.
        # Keeping the binding outside this body also covers exceptions raised
        # by setup, cache handling, or user code before a normal return.
        def _clear_tool_call(value):
            # Kept as a local identity helper for the early-return branches;
            # cleanup is centralized in the wrapper's finally block.
            return value
        passable_kwargs = dict(args)
        # The schema lets null mean "not given" for every optional parameter;
        # one whose type has no None takes its default instead.
        for key, default in null_defaults.items():
            if key in passable_kwargs and passable_kwargs[key] is None:
                passable_kwargs[key] = default
        from openprogram.runtime.jobs.runner import (
            current_job_operation_timeout,
            current_job_operation_timeout_reason,
            record_current_job_activity,
        )
        if accepts_cancel:
            passable_kwargs["cancel"] = cancel_event

        # Streaming on_update — wrap through a bounded tail buffer so a
        # noisy tool can't grow without limit. The wrapper still forwards
        # text to the dispatcher callback in real time; the accumulator
        # is there as a memory guard, not a queue.
        accumulator = _TailAccumulator(
            stream_capacity_chars
            if stream_capacity_chars is not None
            else DEFAULT_STREAM_CAPACITY_CHARS
        )
        if accepts_on_update:
            def _on_update(text: str) -> None:
                accumulator.push(text)
                record_current_job_activity("tool_progress")
                if on_update_cb is not None:
                    try:
                        on_update_cb(text)
                    except Exception:
                        # The caller's progress callback must not break the running tool.
                        _log.debug("tool on_update callback failed", exc_info=True)
            passable_kwargs["on_update"] = _on_update

        # LLM-controllable timeout — clamp into [timeout_min, timeout_max]
        # and forward the clamped value both to wait_for and to the
        # function body so it can self-manage retries within the budget.
        effective_timeout = timeout
        if timeout_is_clampable and "timeout" in args:
            try:
                requested = float(args["timeout"])
            except (TypeError, ValueError):
                requested = None
            if requested is not None:
                lo = timeout_min if timeout_min is not None else 0.0
                hi = (timeout_max if timeout_max is not None
                      else (timeout if timeout is not None else float("inf")))
                clamped = max(lo, min(hi, requested))
                passable_kwargs["timeout"] = clamped
                effective_timeout = clamped

        try:
            effective_timeout = current_job_operation_timeout(
                effective_timeout,
                preemptibility="async" if is_async_fn else "none",
            )
        except Exception as exc:
            if getattr(exc, "reason_code", None) == "error.nonpreemptible_operation":
                return _clear_tool_call(AgentToolResult(
                    content=[TextContent(text=f"[error] {exc}")],
                    details={"reason_code": exc.reason_code},
                    is_error=True,
                ))
            raise
        record_current_job_activity("operation_start")

        denial = dispatch_sandbox_error(
            actual_name, args,
            path_params=path_params, url_params=url_params,
        )
        if denial is not None:
            return _clear_tool_call(denial)

        # Cache check (after timeout clamp — clamp is part of the cache key).
        if cache:
            key = _cache_key(actual_name, args)
            hit = _cache_get(key)
            if hit is not None:
                return _clear_tool_call(hit)

        try:
            raw = await invoke_callable(
                fn, passable_kwargs,
                timeout=effective_timeout,
                is_async=is_async_fn,
            )
        except asyncio.TimeoutError:
            reason_code = current_job_operation_timeout_reason(effective_timeout)
            return _clear_tool_call(timeout_tool_result(
                actual_name, effective_timeout,
                details={
                    "timeout": True,
                    "reason_code": reason_code or "error.operation_timeout",
                },
            ))
        except asyncio.CancelledError:
            _clear_tool_call(None)
            raise
        except Refused as refused:
            from openprogram.runtime.agent.turn_request import get_turn_request
            return _clear_tool_call(refused.result(mode=getattr(get_turn_request(), "permission_mode", None)))
        except Exception as e:
            return _clear_tool_call(AgentToolResult(
                content=[TextContent(text=f"[error] {type(e).__name__}: {e}")],
                details={"trace": traceback.format_exc()[:2000]},
                is_error=True,
            ))

        result = _normalize_result(
            raw, call_id=call_id,
            cap=max_result_weight,
        )

        if cache and not result.is_error:
            _cache_set(_cache_key(actual_name, args), result, cache_ttl)

        return _clear_tool_call(result)

    async def _execute(call_id: str,
                       args: dict[str, Any],
                       cancel_event,        # asyncio.Event | None
                       on_update_cb) -> AgentToolResult:  # callable | None
        """Bind the provider id for the whole tool call and always clear it."""
        tool_call_token = _current_tool_call_id.set(call_id)
        identity_token = _tool_call_identity_consumed.set(False)
        try:
            return await _execute_bound(call_id, args, cancel_event, on_update_cb)
        finally:
            _current_tool_call_id.reset(tool_call_token)
            _tool_call_identity_consumed.reset(identity_token)

    agent_tool = _build_and_register_tool(
        name=actual_name,
        description=actual_description,
        parameters=actual_parameters,
        label=label,
        execute=_execute,
        requires_approval=requires_approval,
        accept_edits_safe=accept_edits_safe,
        check_fn=check_fn,
        requires_env=requires_env,
        can_use=can_use,
        describe=describe,
        defer=defer,
        toolsets=toolset,
        unsafe_in=unsafe_in,
        expose=expose,
        register_globally=register_globally,
    )
    setattr(agent_tool, "_source_module", fn.__module__)
    return agent_tool


def _build_and_register_tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    label: Optional[str],
    execute: Callable,
    requires_approval: Any = None,
    accept_edits_safe: bool = False,
    check_fn: Optional[Callable[[], bool]] = None,
    requires_env: Any = (),
    can_use: Optional[Callable[[], bool]] = None,
    describe: Optional[Callable[[], dict[str, Any]]] = None,
    defer: bool = False,
    toolsets: Any = (),
    unsafe_in: Any = (),
    expose: bool = True,
    register_globally: bool = True,
) -> AgentTool:
    """Single source of truth for "build AgentTool + attach sidecars +
    register".

    Used by both ``@function`` (called inline at the end of the
    decorator body) and ``register_agent_method`` (called
    after the wrapper is constructed). Keeping the construction in one
    place means the sidecar contract — which attrs are required, what
    types they hold — has one definition. Adding a new gating layer
    or sidecar attr in the future only requires editing this helper;
    both registration paths use it.
    """
    agent_tool = AgentTool(
        name=name,
        description=description,
        parameters=parameters,
        label=label or name,
        execute=execute,
    )
    # Dispatcher reads ``_requires_approval`` via ``tool_requires_approval``;
    # the gating triad (_check_fn / _requires_env / _can_use) is read by
    # ``is_available_agent_tool`` for the 4th of the 6 selection layers.
    setattr(agent_tool, "_requires_approval", requires_approval)
    from openprogram.runtime.conversation.checkpoint import _callable_descriptor
    implementation = _callable_descriptor(execute)
    if implementation is not None:
        setattr(agent_tool, "_runtime_implementation", implementation)
    # acceptEdits 档下自动放行的"写安全"工具（read/write/edit 等）。命令类
    # （bash/exec）保持 False，acceptEdits 下仍审批。见 docs/reference/design/runtime/security/permission-decision.html。
    setattr(agent_tool, "_accept_edits_safe", bool(accept_edits_safe))
    setattr(agent_tool, "_check_fn", check_fn)
    setattr(agent_tool, "_requires_env", tuple(requires_env))
    setattr(agent_tool, "_can_use", can_use)
    setattr(agent_tool, "_describe", describe)
    # Stream/DAG projections need the function's visibility policy before the
    # body enters. ``@function`` only has a boolean tool exposure switch;
    # false means no persisted node, while the agentic decorator overwrites
    # this with its richer io/llm/full/hidden policy below.
    setattr(agent_tool, "_dag_expose", "full" if expose else "hidden")
    # The exposure tier; ``split_tools_for_dispatch`` reads it to decide
    # whether to ship the full schema in the provider tools array or leave
    # it to be loaded later via ``tool_search``.
    set_tier(agent_tool, Tier.HIDDEN if not expose
             else Tier.DEFERRED if defer else Tier.RESIDENT)

    if register_globally:
        register(agent_tool, toolsets=list(toolsets), unsafe_in=list(unsafe_in),
                 expose=expose)

    return agent_tool


# ---------------------------------------------------------------------------
# Dispatcher hook — read approval policy off a tool
# ---------------------------------------------------------------------------

def tool_requires_approval(t: AgentTool, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Resolve a tool's approval policy for these args. Used by the
    dispatcher right before executing the tool."""
    policy = getattr(t, "_requires_approval", None)
    return _evaluate_approval(policy, args)


# ---------------------------------------------------------------------------
# Layer 6 — deferred loading + ToolSearch
# ---------------------------------------------------------------------------
#
# Claude Code's design: when the tool catalog gets large (30+ in their
# build, more once MCP servers attach), shipping every tool's full
# JSON Schema in the prompt every turn becomes wasteful. They mark
# rarely-used tools as ``shouldDefer=true``; deferred tools appear in
# the *catalog listing* of the system prompt as
# ``<name>: <one-line description>`` but their parameter schema is
# **not** included in the provider's tools array. The model has to
# call ``tool_search(select="<name>,<other>")`` to load the schemas;
# the next request includes the now-loaded tools' full schemas.
#
# What is loaded is not kept anywhere: each request reads it from its own
# messages (``loaded_tools``), the load records of its tool results and the
# names a compaction summary among them carries. ``tool_search`` is the one
# writer of a record (``record_loads``): a guide or an opened application
# names its tools and the model loads them with ``select:`` like any other.
# Design: docs/reference/design/function/deferred-tools.html

#: The loaded set of the request this loop sent last, as that request read
#: it from its messages. A failed call of a deferred tool reads it to say
#: the schema was not sent (``schema_not_sent_hint``); it decides nothing
#: about the next request.
_request_loaded: contextvars.ContextVar[frozenset[str]] = (
    contextvars.ContextVar("_request_loaded", default=frozenset())
)


def loaded_tools(messages: Any) -> list[str]:
    """Names of the deferred tools ``messages`` loaded (``shared.tool_loads``)."""
    from openprogram.models.providers.shared.tool_loads import loaded_tools as _loaded

    return _loaded(messages)


def note_request_loaded(names: Any) -> None:
    """Record the loaded set of the request about to be sent (``_request_loaded``)."""
    _request_loaded.set(frozenset(names or ()))


#: The applications open in a window for the current session, by id. Read
#: by the page; an open application's tools stay deferred like any other.
_open_applications: contextvars.ContextVar[frozenset[str]] = (
    contextvars.ContextVar("_open_applications", default=frozenset())
)


def install_open_applications(app_ids: Any) -> Any:
    """Install the ids of the applications open for this session; returns the token."""
    return _open_applications.set(frozenset(app_ids or ()))


def resident_now(t: Any, loaded: Any = None) -> bool:
    """Whether ``t``'s schema goes into this request's tools array.

    A resident tool always does; a deferred one once ``loaded`` names it
    (by default the last request's loaded set). Opening the application
    that owns it loads nothing: the model loads it by name.
    """
    tier = tool_tier(t)
    if tier is Tier.RESIDENT:
        return True
    if tier is not Tier.DEFERRED:
        return False
    if loaded is None:
        loaded = _request_loaded.get()
    return getattr(t, "name", None) in loaded


#: Why a tool a conversation may have loaded is gone, by name: its MCP server
#: disconnected, its package was removed. Cleared when it registers again.
_departed: dict[str, str] = {}

#: What a call of a loaded tool that is gone says when no reason was kept.
UNAVAILABLE_DEFAULT = "it is no longer available"


def mark_unavailable(names: Any, reason: str) -> None:
    """Record why the tools ``names`` are leaving the registry."""
    for name in names or ():
        if isinstance(name, str) and name:
            _departed[name] = reason


def unavailable_reason(name: str) -> str:
    return _departed.get(name) or UNAVAILABLE_DEFAULT


def unavailable_text(name: str) -> str:
    """What the model reads when it calls a loaded tool that is gone."""
    return f"Tool {name} is unavailable: {unavailable_reason(name)}."


def unavailable_tool(name: str, definition: dict[str, Any]) -> AgentTool:
    """A loaded tool whose source is gone, as the request still sends it.

    Its definition is the one its load record kept, so the tools array
    does not shrink (a removed entry is a rejected request on Anthropic and
    a re-read of everything on every provider); a call of it fails with
    why (Codex answers an unknown tool the same way, ``unsupported call``).
    """
    async def _execute(call_id, args, cancel, on_update):
        return AgentToolResult(content=[TextContent(text=unavailable_text(name))],
                               details={}, is_error=True)

    placeholder = AgentTool(
        name=name, description=str(definition.get("description") or ""),
        parameters=dict(definition.get("parameters") or {"type": "object", "properties": {}}),
        label=name, execute=_execute,
    )
    setattr(placeholder, "_requires_approval", False)
    setattr(placeholder, "_check_fn", None)
    setattr(placeholder, "_requires_env", ())
    setattr(placeholder, "_can_use", None)
    setattr(placeholder, "_unavailable", True)
    set_tier(placeholder, Tier.DEFERRED)
    return placeholder


def schema_not_sent_hint(name: str) -> str:
    """Added to a failed argument check of a deferred tool the request had
    not loaded (Claude Code ``buildSchemaNotSentHint``)."""
    return (f"This tool's schema was not sent. Load it with `tool_search select:{name}`, "
            "then retry.")


#: The tools this turn sends with their schema whatever their registered
#: tier (a deferred tool the turn made resident, ``as_resident``): the allowed
#: set they were installed with and their names. Read only while that set is
#: the one in force, so a scope installed any other way never inherits them.
_turn_resident_names: contextvars.ContextVar[tuple[Any, frozenset[str]]] = contextvars.ContextVar(
    "_turn_resident_names", default=(None, frozenset()),
)


def install_allowed_tool_names(names: set[str], resident: Any = ()) -> Any:
    """Limit deferred discovery to the resolver's current allowed set.

    ``resident`` names the tools of that set the turn's tools array holds
    with their schema; a search for one answers that it is callable
    (``callable_now``) instead of loading it a second time."""
    allowed = set(names)
    _turn_resident_names.set((allowed, frozenset(resident or ())))
    return _allowed_tool_names.set(allowed)


def install_turn_tools(tools: Any) -> Any:
    """``install_allowed_tool_names`` for a turn's resolved tool list."""
    tools = list(tools or ())
    return install_allowed_tool_names(
        {t.name for t in tools},
        resident={t.name for t in tools if tool_tier(t) is Tier.RESIDENT},
    )


def callable_now(t: Any) -> bool:
    """Whether the model can call ``t`` without a search: it is resident,
    by its registered tier or as this turn sends it."""
    if tool_tier(t) is Tier.RESIDENT:
        return True
    allowed, resident = _turn_resident_names.get()
    return (allowed is not None and allowed is _allowed_tool_names.get()
            and getattr(t, "name", None) in resident)


def split_tools_for_dispatch(
    tools: list[AgentTool],
    loaded: Any = None,
    records: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[list[AgentTool], list[tuple[str, str]]]:
    """Partition an AgentTool list for the dispatcher.

    Returns ``(provider_tools, deferred_catalog)`` where:

      - ``provider_tools`` is the subset to ship with full JSON Schema
        in the provider's tools array. Every non-deferred tool plus
        every deferred tool named in ``loaded`` — the request's
        ``loaded_tools(messages)``; by default the last request's. With
        ``records`` (``tool_loads.loaded_records``), a loaded tool that
        is no longer offered stays, as ``unavailable_tool``.
      - ``deferred_catalog`` is ``[(name, description), ...]`` for
        every deferred tool offered now, loaded or not.
    """
    loaded = _request_loaded.get() if loaded is None else frozenset(loaded)
    provider_tools: list[AgentTool] = []
    catalog: list[tuple[str, str]] = []
    offered: set[str] = set()
    for t in tools:
        offered.add(t.name)
        if resident_now(t, loaded):
            provider_tools.append(t)
        if tool_tier(t) is Tier.DEFERRED:
            catalog.append((t.name, t.description))
    for name, definition in (records or {}).items():
        if name in loaded and name not in offered and definition:
            provider_tools.append(unavailable_tool(name, definition))
    return provider_tools, catalog


_TOOL_SEARCH_MAX_RESULTS = 5


def record_loads(details: Optional[dict[str, Any]], tools: list[AgentTool]) -> dict[str, Any]:
    """Write a load record for ``tools`` into a tool result's ``details``.

    The one writer of ``details["loads"]`` (``shared.tool_loads``): each
    tool's definition as it is now, so the history that loaded it keeps the
    definition the model was first sent. A name no provider accepts is not
    recorded. Returns the updated ``details``.
    """
    from openprogram.models.providers.shared.tool_loads import (
        LOADS, tool_definition, valid_tool_name,
    )
    details = dict(details or {})
    records = list(details.get(LOADS) or ())
    seen = {entry.get("name") for entry in records if isinstance(entry, dict)}
    for t in tools:
        t = current_tool(t)
        if not valid_tool_name(t.name) or t.name in seen:
            continue
        seen.add(t.name)
        records.append({"name": t.name, "definition": tool_definition(t)})
    if records:
        details[LOADS] = records
    return details


def _tool_search_impl(select: str, *, max_results: int = _TOOL_SEARCH_MAX_RESULTS) -> str:
    """The text of a ``tool_search`` call (``_tool_search_run``)."""
    return _tool_search_run(select, max_results=max_results)[0]


def _tool_search_run(
    select: str, *, max_results: int = _TOOL_SEARCH_MAX_RESULTS,
) -> tuple[str, list[AgentTool]]:
    """The text of a ``tool_search`` call and the tools it loaded."""
    text, loaded, _note = _tool_search(select, max_results=max_results)
    return text, loaded


def _tool_search(
    select: str, *, max_results: int = _TOOL_SEARCH_MAX_RESULTS,
) -> tuple[str, list[AgentTool], str]:
    """Argument format mirrors Claude Code's ``ToolSearch``:

      ``select:name1,name2,name3``  — explicit names, comma-separated
                                     (``select:`` prefix optional when
                                     the query is itself a comma list).
      ``notebook jupyter``           — keyword search; up to
                                     ``max_results`` best matches.
      ``+slack send``                — require ``slack`` in the tool
                                     name, rank remaining terms.

    Returns the result text, the tools the call loaded and the note: what
    the call has to say beyond the definitions it loaded (a requested tool
    is already callable, a name is unknown, nothing matched). The caller
    records the loads and the note on the result (``record_loads``,
    ``record_note``), because a provider that sends a search's result as
    definitions alone never sends its text.

    After this call the matched tools' full schemas appear in the
    provider tools array on the next provider request. Calling a deferred tool
    *before* it has been loaded triggers an InputValidationError on
    most providers because the schema isn't in the request.
    """
    payload = select.strip()
    if not payload:
        text = "Error: pass tool names like `select:name1,name2` or a keyword query."
        return text, [], text

    # Explicit-name path: select: prefix, or input looks like a pure
    # comma-list of identifiers ("foo, bar, baz" with no whitespace
    # tokens that would suggest a query).
    explicit = payload.startswith("select:")
    if explicit:
        payload_for_names = payload[len("select:"):].strip()
    else:
        # Heuristic: if there are commas and no spaces around the commas,
        # treat as explicit names. A keyword query is space-separated.
        looks_like_names = (
            "," in payload
            and " " not in payload.replace(", ", "")
        )
        if looks_like_names:
            explicit = True
            payload_for_names = payload

    if explicit:
        return _tool_search_by_name(payload_for_names)

    return _tool_search_by_keyword(payload, max_results=max_results)


def record_note(details: Optional[dict[str, Any]], note: str) -> Optional[dict[str, Any]]:
    """``details`` with a search's note (``shared.tool_loads.NOTE``)."""
    if not note:
        return details
    from openprogram.models.providers.shared.tool_loads import NOTE

    return {**(details or {}), NOTE: note}


def _tool_schema_line(t: AgentTool) -> str:
    """Render one loaded tool as a callable definition.

    The next provider request also includes the resolved tool schema.
    This result uses the same encoding to describe the discovered tool.
    """
    import json as _json
    t = current_tool(t)
    try:
        params = _json.dumps(t.parameters, ensure_ascii=False, default=str)
    except Exception:
        params = "{}"
    return (f'{{"name": "{t.name}", "description": {_json.dumps(t.description or "", ensure_ascii=False)}, '
            f'"parameters": {params}}}')


_SCHEMA_PREAMBLE = (
    "Their full schemas follow. You can call these tools immediately, in "
    "THIS turn, by constructing the call from the schema below."
)


def already_callable_text(name: str) -> str:
    """What a search for a tool the model already has answers.

    Claude Code answers such a ``select:`` with the tool itself, "a harmless
    no-op that lets the model proceed"; the sentence says the same and
    starts with the fact, so it cannot be read as a failed search."""
    return (f"`{name}` is already callable: it is in your tools list with its full "
            "schema. Call it now; it never needs tool_search.")


#: Added to a search that found nothing, so "no match" is not read as
#: "no such tool" for a tool the model already has.
_NO_SEARCH_NEEDED = (
    "Only the tools named in the deferred tools list are loaded by a search; "
    "a tool in your tools list is callable as it is."
)


def _in_scope(name: str) -> Optional[AgentTool]:
    """The tool ``name`` names inside this turn's allowed set, or None.

    A Claude-style ToolSearch request may capitalize built-in names.
    Resolve only inside this turn's allowed set; never broaden scope."""
    allowed = _allowed_tool_names.get()
    if (allowed is not None and name not in allowed and get(name) is None
            and name.lower() in allowed):
        name = name.lower()
    return get(name) if allowed is None or name in allowed else None


def _tool_search_by_name(payload: str) -> tuple[str, list[AgentTool], str]:
    requested = [n.strip() for n in payload.split(",") if n.strip()]
    if not requested:
        text = "Error: pass tool names like `select:name1,name2`."
        return text, [], text

    loaded: list[AgentTool] = []
    missing: list[str] = []
    notes: list[str] = []
    for name in requested:
        t = _in_scope(name)
        if t is None:
            missing.append(name)
        elif callable_now(t):
            notes.append(already_callable_text(t.name))
        elif t not in loaded:
            # A tool the request already loaded is loaded again: its
            # definition comes back and is recorded (Claude Code returns
            # the reference).
            loaded.append(t)

    if missing:
        notes.append(f"No tool named {', '.join(missing)} is offered in this conversation. "
                     + _NO_SEARCH_NEEDED)
    parts = list(notes)
    if loaded:
        parts.append(f"Loaded {len(loaded)} deferred tool"
                     f"{'s' if len(loaded) != 1 else ''}. {_SCHEMA_PREAMBLE}")
        parts.append("<functions>")
        parts.extend(f"<function>{_tool_schema_line(t)}</function>" for t in loaded)
        parts.append("</functions>")
    return "\n".join(parts), loaded, "\n".join(notes)


def _namespace_of(t: Any) -> str:
    """The MCP server or application a deferred tool belongs to, or ""."""
    return str(getattr(t, "_mcp_server", None) or application_of(tool_owner(t)) or "")


def _tool_search_by_keyword(query: str, *, max_results: int) -> tuple[str, list[AgentTool], str]:
    """Rank the deferred tools in scope against ``query`` and load the top
    hits (``tool_rank``: Pi's BM25 over name, description, search hint,
    schema and namespace).

    A ``+term`` is a hard filter on the tool name, applied first; the other
    terms rank what is left. A query of ``+term`` alone loads every tool
    whose name has it, in name order. A tool the request already loaded is
    ranked and loaded like the others. A query that is exactly the name of
    a tool in scope is that tool alone, never a ranking (Claude Code's
    exact-name fast path): a deferred one is loaded, one the model already
    has is answered as callable.
    """
    from openprogram.programs._host.tool_rank import document, rank

    raw_terms = [t for t in query.split() if t.strip()]
    required = [t[1:].lower() for t in raw_terms if t.startswith("+") and len(t) > 1]
    weighted = [t for t in raw_terms if not t.startswith("+")]
    if not raw_terms:
        return "Error: empty query.", [], "Error: empty query."

    # A query that is exactly one tool's name names that tool: it is loaded
    # alone, as ``select:`` would, and nothing is ranked.
    if _in_scope(query.strip()) is not None:
        return _tool_search_by_name(query.strip())

    allowed = _allowed_tool_names.get()
    candidates = sorted(
        (t for t in all_tools()
         if tool_tier(t) is Tier.DEFERRED and not callable_now(t)
         and (allowed is None or t.name in allowed)
         and all(req in t.name.lower() for req in required)),
        key=lambda t: t.name,
    )
    if weighted:
        by_name = {t.name: t for t in candidates}
        ranked = rank(" ".join(weighted), [
            document(t.name, t.description or "", t.parameters,
                     getattr(t, "_search_hint", None) or "", _namespace_of(t))
            for t in (current_tool(t) for t in candidates)
        ], len(candidates))
        matched = [by_name[name] for name, _score in ranked]
    else:
        matched = candidates

    if not matched:
        text = f"No deferred tools matched query {query!r}. {_NO_SEARCH_NEEDED}"
        return text, [], text

    top = matched[:max_results]
    head = (
        f"Loaded {len(top)} deferred tool"
        f"{'s' if len(top) != 1 else ''} matching {query!r} "
        f"(top {len(top)} of {len(matched)} matches). "
        f"{_SCHEMA_PREAMBLE}"
    )
    lines = ["<functions>"]
    lines += [f"<function>{_tool_schema_line(t)}</function>" for t in top]
    lines.append("</functions>")
    return head + "\n" + "\n".join(lines), top, ""


# Build + register the ToolSearch entry point manually (rather than
# @function) so we don't depend on it being decorated like a normal
# tool — it's a load primitive, not a user feature, and it must never
# defer itself.

async def _tool_search_execute(call_id, args, cancel, on_update):
    if not isinstance(args, dict):
        args = {}
    select = str(args.get("select") or "")
    raw_max = args.get("max_results")
    try:
        max_results = int(raw_max) if raw_max is not None else _TOOL_SEARCH_MAX_RESULTS
    except (TypeError, ValueError):
        max_results = _TOOL_SEARCH_MAX_RESULTS
    max_results = max(1, min(max_results, 20))
    text, loaded, note = _tool_search(select, max_results=max_results)
    return AgentToolResult(content=[TextContent(text=text)],
                           details=record_note(record_loads(None, loaded) if loaded else None, note))


tool_search = AgentTool(
    name="tool_search",
    description=(
        "Load the schema of a deferred tool so you can call it. Deferred "
        "tools are the ones named in the deferred tools list: only their "
        "names are sent at the start. A tool already in your tools list is "
        "callable as it is and never needs a search. This returns the "
        "matching deferred tools with full schemas, so you can call them "
        "immediately in the same turn.\n"
        "\n"
        "Query forms:\n"
        "  `select:name1,name2`  — load exactly these tools by name; a "
        "tool's name alone does the same.\n"
        "  `notebook jupyter`    — keyword search; up to 5 best matches "
        "ranked by tool name, description, parameters and server.\n"
        "  `+slack send`         — `+`-prefixed terms HARD-filter on "
        "the name; remaining terms rank within survivors."
    ),
    parameters={
        "type": "object",
        "properties": {
            "select": {
                "type": "string",
                "description": (
                    "Either `select:name1,name2` (or one exact tool name) "
                    "to load exactly those tools, "
                    "or a free-text keyword query (space-separated, "
                    "use `+keyword` to require a term in the tool name)."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": (
                    "For keyword queries: how many top matches to load "
                    "(default 5)."
                ),
            },
        },
        "required": ["select"],
    },
    label="tool_search",
    execute=_tool_search_execute,
)


setattr(tool_search, "_requires_approval", False)
setattr(tool_search, "_check_fn", None)
setattr(tool_search, "_requires_env", ())
setattr(tool_search, "_can_use", None)
set_tier(tool_search, Tier.RESIDENT)  # the loader never defers itself

register(tool_search,
         toolsets=["default", "core", "research", "browser",
                   "coding", "vision", "memory"])


#: Deferred tool names listed in a prompt's catalog; the rest are counted.
DEFERRED_NAMES_SHOWN = 80

#: How long a catalog line's description may be, and the whole catalog note
#: (Codex ``world_state/tools.rs``: 250 characters, 4 KiB).
CATALOG_DESCRIPTION_CHARS = 250
CATALOG_MAX_BYTES = 4 * 1024
_CATALOG_OMISSION_RESERVE = 64
_CATALOG_TAG = "deferred-tools"

#: What loading a tool promises: it stays while its source does.
_LOAD_PROMISE = (
    "Only these tools need loading: their schemas are not sent at the start. "
    "Before the first call of one of them, call tool_search with its name "
    "(`select:name`) or descriptive keywords; returned schemas can be called "
    "in the same turn, and a loaded tool stays callable while its source is "
    "connected. Every tool already in your tools list is callable as it is; "
    "never search for one."
)


def deferred_catalog_text(catalog: list[tuple[str, str]]) -> str:
    """Deferred-discovery guidance for a prompt of its own (a ``runtime.exec``
    call, an Agent method): the tools' names, never their descriptions or
    schemas. A conversation's turns get the catalog beside their message
    instead (``deferred_catalog`` / ``catalog_note``)."""
    if not catalog:
        return ""
    names = [name for name, _ in catalog]
    shown = ", ".join(names[:DEFERRED_NAMES_SHOWN])
    more = len(names) - DEFERRED_NAMES_SHOWN
    if more > 0:
        shown += f", and {more} more"
    return f"{len(catalog)} deferred tools are available via tool_search: {shown}. {_LOAD_PROMISE}"


def _one_line(text: str, limit: int = CATALOG_DESCRIPTION_CHARS) -> str:
    line = " ".join(((text or "").strip().splitlines() or [""])[0].split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def deferred_catalog(tools: Any, open_applications: Any = ()) -> dict[str, str]:
    """The deferred tools in scope, as the catalog lists them.

    ``{entry: line}``: a built-in deferred tool is its own entry with no
    description; an MCP server's or an application's tools are one entry,
    the namespace, whose line names its tools (cut to 250 characters). An
    application open in a window for the conversation (``open_applications``)
    says so and how to load its tools: opening it loads nothing.
    """
    open_ids = set(open_applications or ())
    groups: dict[str, list[str]] = {}
    plain: dict[str, str] = {}
    for t in tools or ():
        if tool_tier(t) is not Tier.DEFERRED or getattr(t, "_unavailable", False):
            continue
        namespace = _namespace_of(t)
        if namespace:
            groups.setdefault(namespace, []).append(t.name)
        else:
            plain[t.name] = ""
    out = dict(sorted(plain.items()))
    for namespace in sorted(groups):
        names = sorted(groups[namespace])
        if namespace in open_ids:
            from openprogram.programs._authoring.guides import activation_note

            out[namespace] = _one_line("Open in a window. " + activation_note(names))
        else:
            out[namespace] = _one_line(f"{len(names)} tools: " + ", ".join(names))
    return out


def _catalog_rows(rows: dict[str, str], budget: int) -> tuple[list[str], int]:
    """Lines for ``rows`` within ``budget`` bytes: names first, then each
    description as far as the bytes left allow. Returns the lines and how
    many rows did not fit."""
    names = list(rows)
    kept: list[str] = []
    used = 0
    for name in names:
        size = len(f"- {name}\n".encode())
        if used + size > budget - _CATALOG_OMISSION_RESERVE and len(kept) < len(names):
            break
        kept.append(name)
        used += size
    lines = []
    left = budget - _CATALOG_OMISSION_RESERVE - used
    for name in kept:
        description = rows[name]
        line = f"- {name}"
        if description and left > 8:
            extra = f": {description}"
            encoded = extra.encode()
            if len(encoded) > left:
                extra = encoded[: max(0, left - 3)].decode("utf-8", "ignore") + "…"
            left -= len(extra.encode())
            line += extra
        lines.append(line)
    return lines, len(names) - len(kept)


def catalog_note(previous: Optional[dict[str, str]], current: dict[str, str]) -> str:
    """What a turn tells the model about the deferred tools, or "".

    With no ``previous`` (the conversation's first turn, the first after a
    summary) the whole catalog; afterwards only what was added and removed
    since, and nothing when nothing changed (Claude Code's
    ``deferred_tools_delta``, Codex's namespace groups). At most
    ``CATALOG_MAX_BYTES``: names are kept before descriptions, and what
    does not fit is counted.
    """
    if previous is not None and previous == current:
        return ""
    if previous is None and not current:
        return ""
    if previous is None:
        groups = [("Deferred tools you can load with tool_search", dict(current))]
    else:
        added = {k: v for k, v in current.items() if previous.get(k) != v}
        removed = {k: v for k, v in previous.items() if k not in current}
        groups = [("Added deferred tools", added), ("Removed deferred tools", removed)]
        if not added and not removed:
            return ""
    open_tag, close_tag = f"<{_CATALOG_TAG}>", f"</{_CATALOG_TAG}>"
    tail = _LOAD_PROMISE if previous is None else (
        "A removed tool can no longer be loaded; a call of one already loaded "
        "says it is unavailable.")
    if not current:
        tail = "No deferred tools remain."
    budget = CATALOG_MAX_BYTES - len(f"{open_tag}\n\n{tail}\n{close_tag}".encode())
    out = [open_tag]
    present = [(label, rows) for label, rows in groups if rows]
    for label, rows in present:
        share = budget // len(present) - len(f"{label}:\n".encode())
        lines, omitted = _catalog_rows(rows, share)
        out.append(f"{label}:")
        out.extend(lines)
        if omitted:
            out.append(f"- … and {omitted} more (find them with a keyword query)")
    out.append("")
    out.append(tail)
    out.append(close_tag)
    return "\n".join(out)
