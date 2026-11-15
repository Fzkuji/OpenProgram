"""The request body each adapter sends, captured where it is serialized.

Every adapter calls :func:`capture_request` with the body it is about to
send, after ``on_payload`` has had its say and before any byte leaves the
process. That one call is the only place a request's wire bytes are
observed; whatever needs them reads them from here:

* the prefix check (``models/usage/prefix_fingerprint.py``) compares each
  request with the previous one on the same lane and records where the
  cached prefix broke;
* tests and scripts open :func:`capturing` to collect the bodies, and with
  ``send=False`` stop each request at this point, so a contract test can
  serialize a conversation through every adapter without a network.

A captured body is split into **units** in the order the provider reads a
prompt: the tools, then each system text, then each history item. A request
extends the previous one when the previous one's units are a prefix of its
own; the first unit that differs is where the provider's cache stops
reading. A unit's digest is of its bytes alone, so whether two requests
match never depends on how either was named. A system text is one unit; the
sections the runtime composed it from (when it says how) are kept beside it
as ``(name, end, digest of the text up to end)`` and only name the section
where a changed system text first differs. Breakpoint markers
(``cache_control``, ``cachePoint``) move from request to request by design
and are left out of the units; the lifetime they ask for is reported as a
setting instead.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

_log = logging.getLogger(__name__)

#: Wire families. An adapter names the one its body is written in.
ANTHROPIC = "anthropic-messages"
RESPONSES = "openai-responses"
COMPLETIONS = "openai-completions"
GOOGLE = "google-generative-ai"
BEDROCK = "bedrock-converse"

#: Body keys that travel as transport options, not as request content.
_TRANSPORT_KEYS = frozenset({"extra_headers", "extra_query", "timeout", "stream"})
#: Markers that say where to cache, not what is cached.
_MARKER_KEYS = frozenset({"cache_control"})


class RequestNotSent(Exception):
    """Raised at the capture point when a :func:`capturing` scope asked that
    requests stop there (``send=False``). The adapter reports it as a failed
    request; nothing reached the network."""


@dataclass(frozen=True)
class Unit:
    """One piece of the prompt prefix: ``label`` says what it is
    (``tools:<name>``, ``system:<section>``, ``history:<kind>``) and
    ``text`` is its canonical JSON."""

    label: str
    text: str
    #: For a system text: ``(name, end, digest)`` per section, ``digest``
    #: being of the raw text up to ``end``.
    sections: tuple[tuple[str, int, str], ...] = ()

    @property
    def digest(self) -> str:
        return _digest(self.text)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CapturedRequest:
    """One request as its adapter serialized it."""

    wire: str
    provider: str
    api: str
    model_id: str
    body: dict = field(repr=False)

    def units(self) -> list[Unit]:
        return _UNITS.get(self.wire, _whole_body)(self.body)

    def settings(self) -> dict[str, Any]:
        """The request fields outside the prefix that still decide whether a
        cached prefix is reused: the model, the reasoning effort, the cache
        key and lifetime, the tool choice."""
        return _settings(self.wire, self.body)

    def history_bytes(self) -> list[str]:
        """The history units' canonical JSON, in order."""
        return [unit.text for unit in self.units() if unit.label.startswith("history:")]


# Who is listening ---------------------------------------------------------

_SystemSections = Callable[[str], Optional[list[tuple[str, str]]]]
_system_sections: Optional[_SystemSections] = None


def register_system_sections(namer: _SystemSections) -> None:
    """The runtime says how a system prompt it composed is cut into named
    sections: ``namer(text)`` returns ``[(name, part), ...]`` whose parts
    join back to ``text`` exactly, or ``None`` for a text it did not compose."""
    global _system_sections
    _system_sections = namer


@dataclass
class _Scope:
    captured: list[CapturedRequest]
    send: bool


_scopes: contextvars.ContextVar[tuple[_Scope, ...]] = contextvars.ContextVar(
    "op_request_capture_scopes", default=())


class RequestSlot:
    """Where the request a stream attempt sends is handed back to it.

    The stream chokepoint binds a slot before it calls the adapter
    (:func:`bind_slot`); the adapter's :func:`capture_request` fills it and
    runs ``on_capture``, so the attempt can check the request before it goes
    out and settle the check when the reply comes back. A slot takes one
    request; a later capture in the same context (an adapter called outside
    the chokepoint) finds it taken and leaves it alone. When the attempt
    sends its request again, mended after the provider refused its shape
    (``utils/request_repair.py``), it reopens the slot first
    (:func:`reopen_slot`), so the slot holds the request that was answered."""

    def __init__(self, on_capture: Optional[Callable[[CapturedRequest], Any]] = None) -> None:
        self.request: Optional[CapturedRequest] = None
        self.result: Any = None
        self._on_capture = on_capture

    def _take(self, request: CapturedRequest) -> None:
        if self.request is not None:
            return
        self.request = request
        if self._on_capture is not None:
            try:
                self.result = self._on_capture(request)
            except Exception:
                # Observing a request must never stop it.
                _log.warning("request capture hook failed for %s", request.model_id, exc_info=True)


_slot: contextvars.ContextVar[Optional[RequestSlot]] = contextvars.ContextVar(
    "op_request_slot", default=None)


def bind_slot(slot: RequestSlot) -> None:
    """Make ``slot`` the one the next captured request in this context fills.

    Set without a reset token on purpose: the chokepoint is an async
    generator, which runs in its consumer's context, so a token could be
    reset from another context. Each attempt binds a fresh slot, and a slot
    takes only one request."""
    _slot.set(slot)


def reopen_slot() -> None:
    """The current attempt sends its request again: the next capture
    replaces the request its slot holds and is checked in its place."""
    slot = _slot.get()
    if slot is not None:
        slot.request = None
        slot.result = None


@contextmanager
def capturing(*, send: bool = True) -> Iterator[list[CapturedRequest]]:
    """Collect every request captured inside the block, in order.

    With ``send=False`` each request stops at the capture point
    (:class:`RequestNotSent`), after it was collected."""
    scope = _Scope(captured=[], send=send)
    token = _scopes.set((*_scopes.get(), scope))
    try:
        yield scope.captured
    finally:
        _scopes.reset(token)


_observers: list[Callable[[CapturedRequest], Any]] = []


@contextmanager
def observing(observer: Callable[[CapturedRequest], Any]) -> Iterator[None]:
    """Hand every request captured in this process, on any thread or task,
    to ``observer`` while the block runs.

    :func:`capturing` follows one context; a process that runs a single job
    (``--print``) and wants what that job's requests carried on the wire,
    wherever the runtime makes them, observes the whole process instead.
    ``observer`` is called where the request is captured, so it can read
    the request's usage context; it must be quick and keep only what it
    needs, never the body. An observer that raises is logged and never
    stops the request."""
    _observers.append(observer)
    try:
        yield
    finally:
        _observers.remove(observer)


def capture_request(model: Any, body: Any, *, wire: str) -> None:
    """Called by an adapter with the body it is about to send."""
    scopes = _scopes.get()
    slot = _slot.get()
    if not scopes and not _observers and (slot is None or slot.request is not None):
        return
    try:
        request = CapturedRequest(
            wire=wire,
            provider=str(getattr(model, "provider", "") or ""),
            api=str(getattr(model, "api", "") or ""),
            model_id=str(getattr(model, "id", "") or ""),
            body=wire_body(body),
        )
    except Exception:
        _log.warning("request body not captured for %s", getattr(model, "id", model), exc_info=True)
        return
    if slot is not None:
        slot._take(request)
    for scope in scopes:
        scope.captured.append(request)
    for observer in tuple(_observers):
        try:
            observer(request)
        except Exception:
            # Observing a request must never stop it.
            _log.warning("request observer failed for %s", request.model_id, exc_info=True)
    if any(not scope.send for scope in scopes):
        raise RequestNotSent(f"{wire} request to {request.model_id} captured and not sent")


# Body normalization -------------------------------------------------------

def _plain(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    to_dict = getattr(value, "to_json_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, (bytes, bytearray)):
        return {"bytes_sha256": hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return repr(value)


def wire_body(body: Any) -> dict:
    """``body`` as JSON values, as it goes on the wire: transport options
    dropped and ``extra_body`` merged into the top level."""
    plain = json.loads(json.dumps(body, default=_plain, ensure_ascii=False))
    if not isinstance(plain, dict):
        return {"body": plain}
    extra = plain.pop("extra_body", None)
    if isinstance(extra, dict):
        plain.update(extra)
    for key in _TRANSPORT_KEYS:
        plain.pop(key, None)
    return plain


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unmarked(value: Any) -> Any:
    """``value`` without breakpoint markers, at any depth."""
    if isinstance(value, dict):
        return {key: _unmarked(item) for key, item in value.items() if key not in _MARKER_KEYS}
    if isinstance(value, list):
        return [_unmarked(item) for item in value
                if not (isinstance(item, dict) and set(item) == {"cachePoint"})]
    return value


# Units --------------------------------------------------------------------

def _tool_units(tools: Any) -> list[Unit]:
    units = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("defer_loading"):
            # A deferred definition is outside the cached prefix until a
            # tool_reference names it (Claude Code leaves it out of its
            # cache hash too): appending one breaks nothing.
            continue
        spec = tool.get("toolSpec") or tool.get("function") or tool
        name = spec.get("name") if isinstance(spec, dict) else None
        declarations = tool.get("functionDeclarations") or tool.get("function_declarations")
        if not name and isinstance(declarations, list):
            name = ",".join(str(item.get("name")) for item in declarations if isinstance(item, dict))
        units.append(Unit(f"tools:{name or tool.get('type') or 'tool'}", _canonical(_unmarked(tool))))
    return units


def _system_units(text: str) -> list[Unit]:
    """One unit for a system text, with the sections it was composed from."""
    if not text:
        return []
    parts = None
    if _system_sections is not None:
        try:
            parts = _system_sections(text)
        except Exception:
            _log.debug("system prompt sections not named", exc_info=True)
            parts = None
    if not parts or "".join(part for _name, part in parts) != text:
        parts = [("prompt", text)]
    sections, end = [], 0
    for name, part in parts:
        if not part:
            continue
        end += len(part)
        sections.append((str(name), end, _digest(text[:end])))
    return [Unit("system", _canonical(text), tuple(sections))]


def _text_blocks_units(blocks: Any) -> list[Unit]:
    if isinstance(blocks, str):
        return _system_units(blocks)
    units = []
    for block in _unmarked(blocks or []):
        if isinstance(block, dict) and isinstance(block.get("text"), str) and set(block) <= {"type", "text"}:
            units.extend(_system_units(block["text"]))
        elif block:
            units.append(Unit("system:block", _canonical(block)))
    return units


def _block_message_units(messages: Any) -> list[Unit]:
    """Anthropic and Bedrock messages: one unit per content block, so a
    message that grows by a block still extends the one before it."""
    units = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        blocks = _unmarked(content or [])
        for index, block in enumerate(blocks):
            kind = block.get("type") if isinstance(block, dict) else None
            if kind is None and isinstance(block, dict):
                kind = next(iter(block), "block")
            units.append(Unit(f"history:{role}:{kind}",
                              _canonical({"role": role, "first": index == 0, "block": block})))
    return units


def _anthropic_units(body: dict) -> list[Unit]:
    return [*_tool_units(body.get("tools")), *_text_blocks_units(body.get("system")),
            *_block_message_units(body.get("messages"))]


def _responses_units(body: dict) -> list[Unit]:
    units = [*_tool_units(body.get("tools")), *_system_units(body.get("instructions") or "")]
    for index, item in enumerate(body.get("input") or []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if index == 0 and role in ("system", "developer"):
            units.extend(_text_blocks_units(item.get("content")))
            continue
        kind = role if item.get("type") in (None, "message") else item.get("type")
        units.append(Unit(f"history:{kind or 'item'}", _canonical(_unmarked(item))))
    return units


def _completions_units(body: dict) -> list[Unit]:
    units = _tool_units(body.get("tools"))
    for index, message in enumerate(body.get("messages") or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        if index == 0 and role in ("system", "developer"):
            content = message.get("content")
            if isinstance(content, str):
                units.extend(_system_units(content))
            else:
                units.extend(_text_blocks_units(content))
            continue
        units.append(Unit(f"history:{role}", _canonical(_unmarked(message))))
    return units


def _google_units(body: dict) -> list[Unit]:
    request = body.get("request") if isinstance(body.get("request"), dict) else body
    config = request.get("config") if isinstance(request.get("config"), dict) else request
    system = config.get("systemInstruction") or config.get("system_instruction") or {}
    parts = system.get("parts") if isinstance(system, dict) else system
    units = [*_tool_units(config.get("tools")), *_text_blocks_units(parts or [])]
    for content in request.get("contents") or []:
        role = content.get("role", "") if isinstance(content, dict) else ""
        units.append(Unit(f"history:{role}", _canonical(_unmarked(content))))
    return units


def _bedrock_units(body: dict) -> list[Unit]:
    tool_config = body.get("toolConfig") or {}
    return [*_tool_units(tool_config.get("tools")), *_text_blocks_units(body.get("system")),
            *_block_message_units(body.get("messages"))]


def _whole_body(body: dict) -> list[Unit]:
    return [Unit("body", _canonical(_unmarked(body)))]


_UNITS: dict[str, Callable[[dict], list[Unit]]] = {
    ANTHROPIC: _anthropic_units,
    RESPONSES: _responses_units,
    COMPLETIONS: _completions_units,
    GOOGLE: _google_units,
    BEDROCK: _bedrock_units,
}


# Settings -----------------------------------------------------------------

def _first_marker(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        marker = value.get("cache_control")
        if isinstance(marker, dict):
            return marker
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _first_marker(child)
        if found is not None:
            return found
    return None


def _settings(wire: str, body: dict) -> dict[str, Any]:
    settings: dict[str, Any] = {"model": body.get("model") or body.get("modelId")}
    if wire == ANTHROPIC:
        output = body.get("output_config") if isinstance(body.get("output_config"), dict) else {}
        settings["effort"] = _canonical([body.get("thinking"), output.get("effort")])
        marker = _first_marker([body.get("tools"), body.get("system"), body.get("messages")])
        settings["ttl"] = None if marker is None else marker.get("ttl", "5m")
        settings["format"] = _canonical(output.get("format"))
    elif wire == RESPONSES:
        settings["effort"] = _canonical(body.get("reasoning"))
        settings["ttl"] = body.get("prompt_cache_retention")
        settings["cache_key"] = body.get("prompt_cache_key")
        settings["format"] = _canonical(body.get("text"))
    elif wire == COMPLETIONS:
        settings["effort"] = body.get("reasoning_effort")
        settings["cache_key"] = body.get("prompt_cache_key")
        settings["format"] = _canonical(body.get("response_format"))
    elif wire == GOOGLE:
        request = body.get("request") if isinstance(body.get("request"), dict) else body
        config = request.get("config") or request.get("generationConfig") or {}
        settings["effort"] = _canonical(config.get("thinkingConfig") or config.get("thinking_config"))
        settings["cache_key"] = config.get("cachedContent") or config.get("cached_content")
    elif wire == BEDROCK:
        settings["effort"] = _canonical(body.get("additionalModelRequestFields"))
    settings["tool_choice"] = _canonical([body.get("tool_choice"), body.get("parallel_tool_calls")])
    return settings


__all__ = [
    "ANTHROPIC", "BEDROCK", "COMPLETIONS", "GOOGLE", "RESPONSES",
    "CapturedRequest", "RequestNotSent", "RequestSlot", "Unit",
    "bind_slot", "capture_request", "capturing", "observing",
    "register_system_sections", "reopen_slot", "wire_body",
]
