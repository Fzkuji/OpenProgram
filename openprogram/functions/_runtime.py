"""@function decorator + runtime layer.

Single-format function definitions. Authors write:

    from openprogram.functions import function

    @function
    async def bash(command: str, timeout: int = 30) -> str:
        '''Run a shell command. Returns combined stdout/stderr/exit_code.

        Args:
            command: Shell command to execute.
            timeout: Max seconds before kill.
        '''
        ...

The decorator returns an ``AgentTool`` instance compatible with
``openprogram.agent.agent_loop`` and registers it into a global
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
    (see ``openprogram/functions/__init__.py``).
  - OpenClaw's per-channel policy — ``unsafe_in=[channel, ...]``
    drops the function from the tool list when the request came
    in on a blacklisted channel (see ``apply_tool_policy``).

Beyond the references this module adds four knobs none of them ship:

  - **Dynamic per-call result ceiling**: the effective char cap is
    ``min(per-function max_result_chars, 30% × context_window)`` so
    a single function can't dominate a small-context model. The
    dispatcher installs the live context window via the
    ``_current_context_window_chars`` ContextVar before each turn;
    if absent, the per-function cap is used straight.
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

All caps are in characters, not tokens — token counting is provider-
dependent and expensive; chars are a reasonable, cheap proxy that
matches all three reference frameworks.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import inspect
import json
import re
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union, get_args, get_origin

from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import ImageContent, TextContent


# ---------------------------------------------------------------------------
# Result type — ergonomic wrapper around AgentToolResult
# ---------------------------------------------------------------------------

@dataclass
class ToolReturn:
    """Optional structured return value. Tools can also return a plain
    str (auto-wrapped as TextContent) or an AgentToolResult directly.

    Use this when a tool needs to return text + images + structured
    JSON together, or to mark itself as an error result without
    raising an exception (for "the LLM should see this as a tool
    error" semantics).
    """
    text: Optional[str] = None
    images: list[Union[bytes, str]] = field(default_factory=list)
    json_data: Any = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# Defaults — match references' values for sanity
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESULT_CHARS = 30_000     # Bash tool default in Claude Code
MIN_KEEP_CHARS = 2_000                 # OpenClaw safety floor
DEFAULT_HEAD_RATIO = 0.7               # 70% head + 30% tail
TOOL_RESULTS_DIRNAME = "tool_results"  # for persist_full mode


def _tool_results_dir() -> Path:
    from openprogram.paths import get_state_dir
    p = get_state_dir() / TOOL_RESULTS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, AgentTool] = {}
_toolset_membership: dict[str, set[str]] = {}      # tool_name → set of toolsets
_unsafe_in_channel: dict[str, set[str]] = {}       # tool_name → set of unsafe-in channels


def register(tool: AgentTool, *, toolsets: list[str] = (),
             unsafe_in: list[str] = ()) -> AgentTool:
    """Register a tool. Same name → overwrite (last import wins).

    `toolsets` lists the named groups this tool belongs to (e.g.
    ["core", "research"]). `unsafe_in` lists channel sources where
    the tool should be hidden by default (e.g. ["wechat"]).
    """
    _registry[tool.name] = tool
    if toolsets:
        _toolset_membership.setdefault(tool.name, set()).update(toolsets)
    if unsafe_in:
        _unsafe_in_channel.setdefault(tool.name, set()).update(unsafe_in)
    return tool


def get(name: str) -> Optional[AgentTool]:
    return _registry.get(name)


def all_tools() -> list[AgentTool]:
    return list(_registry.values())


def filter_for(*, names: Optional[list[str]] = None,
               toolset: Optional[str] = None,
               source: Optional[str] = None) -> list[AgentTool]:
    """Pick tools by name list, toolset name, or both. Excludes any
    tool flagged unsafe in `source`.
    """
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
    _registry.clear()
    _toolset_membership.clear()
    _unsafe_in_channel.clear()


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


def _python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Best-effort conversion. Handles primitives, Optional, list[X],
    dict, Literal[...], Union[A, B] (becomes {"oneOf": [...]}).
    Anything exotic falls back to {} (LLM gets a free-form value)."""
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
            return _python_type_to_json_schema(non_none[0])
        return {"oneOf": [_python_type_to_json_schema(a) for a in non_none]}

    if origin in (list, tuple):
        if args:
            return {"type": "array", "items": _python_type_to_json_schema(args[0])}
        return {"type": "array"}

    if origin is dict:
        return {"type": "object"}

    # Literal[...]
    if hasattr(tp, "__class__") and tp.__class__.__name__ == "_LiteralGenericAlias":
        return {"enum": list(args)}

    return {}


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
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        **({"required": required} if required else {}),
    }


# ---------------------------------------------------------------------------
# Result truncation
# ---------------------------------------------------------------------------

def _cap_result_text(text: str, max_chars: int,
                     *, head_ratio: float = DEFAULT_HEAD_RATIO) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(max_chars, MIN_KEEP_CHARS)
    head = int(keep * head_ratio)
    tail = keep - head
    elided = len(text) - head - tail
    return (
        text[:head]
        + f"\n\n[... {elided:,} chars elided of {len(text):,} total —"
        f" call again with narrower scope or check the persisted file ...]\n\n"
        + text[-tail:]
    )


def _persist_full_result(call_id: str, text: str) -> Path:
    p = _tool_results_dir() / f"{call_id}.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _normalize_result(raw: Any, *, call_id: str, max_chars: int,
                      persist_full: bool, head_ratio: float) -> AgentToolResult:
    """Convert tool's raw return value into an AgentToolResult.

    Accepted shapes:
      - str → TextContent
      - dict / list → JSON-serialized as TextContent
      - ToolReturn → text + images + json
      - AgentToolResult → passthrough

    Then applies char cap with optional persist-to-disk for the full
    version (so the LLM can lazy-load via a read tool when needed).
    """
    if isinstance(raw, AgentToolResult):
        return raw

    images: list[ImageContent] = []
    is_error = False
    json_payload: Any = None
    text_part: Optional[str] = None

    if isinstance(raw, ToolReturn):
        text_part = raw.text
        is_error = raw.is_error
        json_payload = raw.json_data
        for img in raw.images:
            if isinstance(img, bytes):
                import base64
                b64 = base64.b64encode(img).decode("ascii")
                images.append(ImageContent(data=b64, media_type="image/png"))
            elif isinstance(img, str):
                # Assume already-base64 or URL — let the provider sort it out
                images.append(ImageContent(data=img, media_type="image/png"))
    elif isinstance(raw, str):
        text_part = raw
    elif raw is None:
        text_part = ""
    else:
        try:
            text_part = json.dumps(raw, ensure_ascii=False, default=str)
        except Exception:
            text_part = repr(raw)

    if text_part is None:
        text_part = ""

    if json_payload is not None and not text_part:
        try:
            text_part = json.dumps(json_payload, ensure_ascii=False, default=str)
        except Exception:
            pass

    # Apply cap; optionally persist full version.
    full_text = text_part
    if len(full_text) > max_chars:
        if persist_full:
            try:
                p = _persist_full_result(call_id, full_text)
                marker = f"\n\n[Full result ({len(full_text):,} chars) saved at {p} — read tool can fetch it]"
            except Exception:
                marker = ""
            text_part = _cap_result_text(full_text, max_chars,
                                          head_ratio=head_ratio) + marker
        else:
            text_part = _cap_result_text(full_text, max_chars,
                                          head_ratio=head_ratio)

    content: list[Any] = []
    if text_part:
        content.append(TextContent(text=text_part))
    content.extend(images)
    if not content:
        content.append(TextContent(text=""))

    details: dict[str, Any] = {}
    if is_error:
        details["is_error"] = True
    if json_payload is not None:
        details["json"] = json_payload

    return AgentToolResult(content=content, details=details or None)


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
# Dynamic per-call ceiling
# ---------------------------------------------------------------------------
#
# Dispatcher installs the live provider's context window (in characters)
# before each turn so a small-context model can't be drowned by a
# single 200k-char tool result. When absent (standalone scripts, tests),
# the per-function cap is used as-is.

_current_context_window_chars: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_current_context_window_chars", default=None)
)


def _effective_max_chars(per_function: int) -> int:
    """The actual cap for this call.

    Returns ``min(per_function, 0.3 × context_window_chars)`` when the
    dispatcher has installed a context-window hint; otherwise the
    decorator's ``max_result_chars`` is used straight. Always floored
    at ``MIN_KEEP_CHARS`` so even a tiny window keeps something useful.
    """
    ctx = _current_context_window_chars.get(None)
    if ctx is None:
        return per_function
    ceiling = max(MIN_KEEP_CHARS, int(ctx * 0.3))
    return min(per_function, ceiling)


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
        self._capacity = max(MIN_KEEP_CHARS, capacity)
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
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    persist_full: bool = False,
    head_ratio: float = DEFAULT_HEAD_RATIO,
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
    requires_approval: Union[bool, Callable[..., Any], None] = None,
    # Selection metadata
    toolset: list[str] = (),               # Hermes — TOOLSETS membership
    unsafe_in: list[str] = (),              # OpenClaw — channel blacklist
    # Layer 1 (Claude Code "conditional import") + Layer 6 ("deferred")
    available_if: Optional[Callable[[], bool]] = None,
    defer: bool = False,
    register_globally: bool = True,
):
    """Wrap a plain function as a registered AgentTool.

    Author writes a normal sync/async Python function with type hints
    and a Google-style docstring. The decorator extracts:

      - name: from override or ``fn.__name__``
      - description: from override or first docstring paragraph
      - parameters JSON schema: from override or fn signature +
        docstring "Args:" section

    Runtime extras (declarative kwargs):

      max_result_chars: per-function char cap. The effective cap for
        any single call is ``min(max_result_chars,
        0.3 × context_window_chars)`` — see ``_effective_max_chars``.
        Over the cap we head+tail truncate with a marker.
      persist_full: when True, oversized results are also saved
        whole to ``~/.agentic/tool_results/<call_id>.txt`` and the
        marker mentions the path. The read tool can fetch it.
      stream_capacity_chars: bound on the streaming on_update tail
        accumulator. Defaults to ``max_result_chars``.

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
        into the next turn's tools array. Matches Claude Code's
        ``shouldDefer`` flag + ToolSearch flow. Use this for tools
        whose schemas are large (MCP tools, niche helpers) so the
        common-path prompt stays cheap; the LLM still discovers
        them by name from the catalog listing.

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
                max_result_chars=max_result_chars,
                persist_full=persist_full, head_ratio=head_ratio,
                stream_capacity_chars=stream_capacity_chars,
                timeout=timeout, timeout_min=timeout_min,
                timeout_max=timeout_max,
                cache=cache, cache_ttl=cache_ttl,
                check_fn=check_fn, requires_env=requires_env,
                can_use=can_use,
                requires_approval=requires_approval,
                toolset=toolset, unsafe_in=unsafe_in,
                available_if=available_if, defer=defer,
                register_globally=register_globally,
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
    actual_parameters = parameters or _build_parameters_schema(fn)
    is_async_fn = inspect.iscoroutinefunction(fn)
    accepts_cancel = "cancel" in sig.parameters
    accepts_on_update = "on_update" in sig.parameters
    accepts_timeout = "timeout" in sig.parameters
    timeout_is_clampable = (
        accepts_timeout
        and (timeout_min is not None or timeout_max is not None)
    )

    async def _execute(call_id: str,
                        args: dict[str, Any],
                        cancel_event,        # asyncio.Event | None
                        on_update_cb) -> AgentToolResult:        # callable | None
        passable_kwargs = dict(args)
        if accepts_cancel:
            passable_kwargs["cancel"] = cancel_event

        # Streaming on_update — wrap through a bounded tail buffer so a
        # noisy tool can't grow without limit. The wrapper still forwards
        # text to the dispatcher callback in real time; the accumulator
        # is there as a memory guard, not a queue.
        accumulator = _TailAccumulator(
            stream_capacity_chars
            if stream_capacity_chars is not None
            else max_result_chars
        )
        if accepts_on_update:
            def _on_update(text: str) -> None:
                accumulator.push(text)
                if on_update_cb is not None:
                    try:
                        on_update_cb(text)
                    except Exception:
                        pass
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

        # Cache check (after timeout clamp — clamp is part of the cache key).
        if cache:
            key = _cache_key(actual_name, args)
            hit = _cache_get(key)
            if hit is not None:
                return hit

        async def _invoke():
            if is_async_fn:
                return await fn(**passable_kwargs)
            loop = asyncio.get_running_loop()
            # Propagate the current Context into the executor thread so
            # tool bodies see ContextVars set on the calling task (e.g.
            # ``_store`` / ``_current_turn_id`` for file_backup,
            # ``_call_id`` for nested @agentic_function attribution).
            # ``run_in_executor`` does not copy context by default.
            ctx = contextvars.copy_context()
            return await loop.run_in_executor(
                None, lambda: ctx.run(fn, **passable_kwargs))

        try:
            if effective_timeout is not None:
                raw = await asyncio.wait_for(_invoke(), timeout=effective_timeout)
            else:
                raw = await _invoke()
        except asyncio.TimeoutError:
            return AgentToolResult(
                content=[TextContent(text=(
                    f"[error] function {actual_name} timed out after "
                    f"{effective_timeout}s"
                ))],
                details={"is_error": True, "timeout": True},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(text=f"[error] {type(e).__name__}: {e}")],
                details={"is_error": True,
                          "trace": traceback.format_exc()[:2000]},
            )

        # Dynamic per-call ceiling — shrinks in small-context models.
        result = _normalize_result(
            raw, call_id=call_id,
            max_chars=_effective_max_chars(max_result_chars),
            persist_full=persist_full,
            head_ratio=head_ratio,
        )

        if cache and not (result.details and result.details.get("is_error")):
            _cache_set(_cache_key(actual_name, args), result, cache_ttl)

        return result

    return _build_and_register_tool(
        name=actual_name,
        description=actual_description,
        parameters=actual_parameters,
        label=label,
        execute=_execute,
        requires_approval=requires_approval,
        check_fn=check_fn,
        requires_env=requires_env,
        can_use=can_use,
        defer=defer,
        toolsets=toolset,
        unsafe_in=unsafe_in,
        register_globally=register_globally,
    )


def _build_and_register_tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    label: Optional[str],
    execute: Callable,
    requires_approval: Any = None,
    check_fn: Optional[Callable[[], bool]] = None,
    requires_env: Any = (),
    can_use: Optional[Callable[[], bool]] = None,
    defer: bool = False,
    toolsets: Any = (),
    unsafe_in: Any = (),
    register_globally: bool = True,
) -> AgentTool:
    """Single source of truth for "build AgentTool + attach sidecars +
    register".

    Used by both ``@function`` (called inline at the end of the
    decorator body) and ``@agentic_function._register_as_tool`` (called
    after the wrapper is constructed). Keeping the construction in one
    place means the sidecar contract — which attrs are required, what
    types they hold — has one definition. Adding a new gating layer
    or sidecar attr in the future only requires editing this helper;
    both decorators pick it up.
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
    setattr(agent_tool, "_check_fn", check_fn)
    setattr(agent_tool, "_requires_env", tuple(requires_env))
    setattr(agent_tool, "_can_use", can_use)
    # Layer 6 — read by ``split_tools_for_dispatch`` to decide whether
    # to ship the full schema in the provider tools array or leave it
    # to be loaded later via ``tool_search``.
    setattr(agent_tool, "_defer", bool(defer))

    if register_globally:
        register(agent_tool, toolsets=list(toolsets), unsafe_in=list(unsafe_in))

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
# the dispatcher tracks the loaded set per session and includes the
# now-loaded tools' full schemas on subsequent turns.
#
# State lives in a ContextVar so the dispatcher can install a fresh
# set at the top of each session and ``tool_search.execute`` can
# mutate it from deep inside the agent loop.

_loaded_deferred: contextvars.ContextVar[Optional[set[str]]] = (
    contextvars.ContextVar("_loaded_deferred", default=None)
)


def install_loaded_deferred(loaded: Optional[set[str]] = None) -> Any:
    """Install a session-scoped 'loaded deferred tool names' set.

    Returns the ContextVar token so the caller can ``reset()`` it on
    session teardown — same idiom as ``_store_var`` in the dispatcher.
    Pass ``loaded=None`` to start fresh; pass a set to restore session
    state across restarts.
    """
    return _loaded_deferred.set(set() if loaded is None else loaded)


def mark_deferred_loaded(names: list[str]) -> set[str]:
    """Add tool names to the current session's loaded-deferred set.

    Returns the updated set. No-ops gracefully when the ContextVar
    wasn't installed (standalone scripts, pre-integration tests).
    """
    current = _loaded_deferred.get()
    if current is None:
        current = set()
        _loaded_deferred.set(current)
    for n in names:
        current.add(n)
    return current


def split_tools_for_dispatch(
    tools: list[AgentTool],
) -> tuple[list[AgentTool], list[tuple[str, str]]]:
    """Partition an AgentTool list for the dispatcher.

    Returns ``(provider_tools, deferred_catalog)`` where:

      - ``provider_tools`` is the subset to ship with full JSON Schema
        in the provider's tools array. Every non-deferred tool plus
        every deferred tool whose name is in the session's loaded set.
      - ``deferred_catalog`` is ``[(name, description), ...]`` for
        every deferred tool *not yet loaded*. The dispatcher pastes
        this into a "deferred tools — call tool_search to load"
        section of the system prompt so the LLM still discovers them.

    When the ContextVar isn't installed (no session) the loaded set
    is empty — all deferred tools land in the catalog.
    """
    loaded = _loaded_deferred.get() or set()
    provider_tools: list[AgentTool] = []
    catalog: list[tuple[str, str]] = []
    for t in tools:
        if not getattr(t, "_defer", False):
            provider_tools.append(t)
        elif t.name in loaded:
            provider_tools.append(t)
        else:
            catalog.append((t.name, t.description))
    return provider_tools, catalog


_TOOL_SEARCH_MAX_RESULTS = 5


def _tool_search_impl(select: str, *, max_results: int = _TOOL_SEARCH_MAX_RESULTS) -> str:
    """Argument format mirrors Claude Code's ``ToolSearch``:

      ``select:name1,name2,name3``  — explicit names, comma-separated
                                     (``select:`` prefix optional when
                                     the query is itself a comma list).
      ``notebook jupyter``           — keyword search; up to
                                     ``max_results`` best matches.
      ``+slack send``                — require ``slack`` in the tool
                                     name, rank remaining terms.

    After this call the matched tools' full schemas appear in the
    provider tools array on the next turn. Calling a deferred tool
    *before* it has been loaded triggers an InputValidationError on
    most providers because the schema isn't in the request.
    """
    payload = select.strip()
    if not payload:
        return "Error: pass tool names like `select:name1,name2` or a keyword query."

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


def _tool_search_by_name(payload: str) -> str:
    requested = [n.strip() for n in payload.split(",") if n.strip()]
    if not requested:
        return "Error: pass tool names like `select:name1,name2`."

    loaded_names: list[str] = []
    missing: list[str] = []
    lines: list[str] = []
    for name in requested:
        t = _registry.get(name)
        if t is None:
            missing.append(name)
            continue
        if not getattr(t, "_defer", False):
            lines.append(f"- {name} (not deferred — already in tools array)")
            continue
        loaded_names.append(name)
        lines.append(f"- {name}: {t.description}")

    if loaded_names:
        mark_deferred_loaded(loaded_names)

    head = (
        f"Loaded {len(loaded_names)} deferred tool"
        f"{'s' if len(loaded_names) != 1 else ''} into the next turn."
        if loaded_names else "Loaded 0 tools."
    )
    if missing:
        lines.append(f"\n[warning] unknown tool name(s): {', '.join(missing)}")
    return head + "\n" + "\n".join(lines)


def _tool_search_by_keyword(query: str, *, max_results: int) -> str:
    """Rank deferred-not-yet-loaded tools against ``query`` and load
    the top hits.

    Scoring (cheap word-overlap, no embeddings):
      * ``+term`` (term prefixed with ``+``) is a HARD filter on the
        tool name — candidates that don't contain it are eliminated.
      * Each remaining word adds points based on where it lands:
        +10 substring in name, +5 in searchHint (set by MCP servers
        via ``_meta['anthropic/searchHint']``), +2 in description.
      * Ties broken alphabetically by name for stable output.
    """
    raw_terms = [t for t in query.split() if t.strip()]
    required = [t[1:].lower() for t in raw_terms if t.startswith("+") and len(t) > 1]
    weighted = [t.lower() for t in raw_terms if not t.startswith("+")]
    if not raw_terms:
        return "Error: empty query."

    # Candidate pool: deferred tools whose schema isn't already in this
    # session's provider tools array.
    loaded = _loaded_deferred.get() or set()
    candidates = [
        t for t in _registry.values()
        if getattr(t, "_defer", False) and t.name not in loaded
    ]

    scored: list[tuple[int, str, AgentTool]] = []
    for t in candidates:
        name_lower = t.name.lower()
        if not all(req in name_lower for req in required):
            continue
        hint = (getattr(t, "_search_hint", None) or "").lower()
        desc = (t.description or "").lower()
        score = 0
        for term in weighted:
            if term in name_lower:
                score += 10
            if term in hint:
                score += 5
            if term in desc:
                score += 2
        # Required-only query (e.g. "+linear") still ranks all matching.
        if not weighted:
            score = 1
        if score > 0:
            scored.append((score, t.name, t))

    if not scored:
        return f"No deferred tools matched query {query!r}."

    scored.sort(key=lambda row: (-row[0], row[1]))
    top = scored[:max_results]
    loaded_names = [name for _, name, _ in top]
    mark_deferred_loaded(loaded_names)

    head = (
        f"Loaded {len(top)} deferred tool"
        f"{'s' if len(top) != 1 else ''} matching {query!r} "
        f"(scored, top {max_results} of {len(scored)} matches):"
    )
    lines = [
        f"- {t.name} (score {score}): {t.description.splitlines()[0]}"
        for score, _, t in top
    ]
    return head + "\n" + "\n".join(lines)


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
    text = _tool_search_impl(select, max_results=max_results)
    return AgentToolResult(content=[TextContent(text=text)])


tool_search = AgentTool(
    name="tool_search",
    description=(
        "Load deferred tools' parameter schemas into the next turn. "
        "Before loading, deferred tools appear by name only in the "
        "system-prompt catalog — calling them raises "
        "InputValidationError because their JSON Schema wasn't sent. "
        "After loading, they're callable normally.\n"
        "\n"
        "Query forms:\n"
        "  `select:name1,name2`  — load these exact tools by name.\n"
        "  `notebook jupyter`    — keyword search; up to 5 best matches "
        "scored against tool name / search hint / description.\n"
        "  `+slack send`         — `+`-prefixed terms HARD-filter on "
        "the name; remaining terms rank within survivors."
    ),
    parameters={
        "type": "object",
        "properties": {
            "select": {
                "type": "string",
                "description": (
                    "Either `select:name1,name2` for exact tool names, "
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
setattr(tool_search, "_defer", False)  # the loader never defers itself

register(tool_search,
         toolsets=["default", "core", "research", "browser",
                   "coding", "vision", "memory"])


def deferred_catalog_text(catalog: list[tuple[str, str]]) -> str:
    """Render the deferred catalog as a system-prompt block.

    Format mirrors what Claude Code injects when deferred tools are
    present, so the LLM recognises the pattern from training:

        The following deferred tools are now available via ToolSearch.
        Their schemas are NOT loaded — calling them directly will fail
        with InputValidationError. Use ToolSearch with query
        "select:<name>[,<name>...]" to load tool schemas before calling
        them:
        <name1>
        <name2>
        ...

    Returns the empty string when ``catalog`` is empty so the caller
    can unconditionally concat it into the prompt.
    """
    if not catalog:
        return ""
    body = "\n".join(name for name, _desc in catalog)
    return (
        "The following deferred tools are now available via ToolSearch.\n"
        "Their schemas are NOT loaded — calling them directly will fail "
        'with InputValidationError. Use ToolSearch with query '
        '"select:<name>[,<name>...]" to load tool schemas before calling '
        "them:\n" + body
    )

