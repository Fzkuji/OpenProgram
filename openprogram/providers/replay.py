"""Replay a recorded provider recording file without touching the network.

:class:`ReplayProvider` reads the JSONL recording file written by
:mod:`openprogram.providers.recording` and serves the recorded events back in
order, one recorded call per provider call. It holds no HTTP client and opens
no socket, so an agent loop driven by it is fully offline.

Each incoming request is compared against the recorded request for the same
call index. The first mismatching field raises :class:`ReplayMismatch`, which
names the call index, the recorded event position and the dotted field path,
plus the recorded and incoming values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from .recording import (
    RECORDING_FORMAT_VERSION,
    _dump_options,
    remove_secret_values,
    restrict_recording_file,
)
from .types import (
    AssistantMessageEvent,
    Context,
    EventDone,
    EventError,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventThinkingStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventToolCallStart,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

_EVENT_CLASSES = {
    "start": EventStart,
    "text_start": EventTextStart,
    "text_delta": EventTextDelta,
    "text_end": EventTextEnd,
    "thinking_start": EventThinkingStart,
    "thinking_delta": EventThinkingDelta,
    "thinking_end": EventThinkingEnd,
    "toolcall_start": EventToolCallStart,
    "toolcall_delta": EventToolCallDelta,
    "toolcall_end": EventToolCallEnd,
    "done": EventDone,
    "error": EventError,
}


class RecordingFileError(RuntimeError):
    """The recording file cannot be parsed as one complete supported recording."""

    def __init__(
        self,
        path: str | Path,
        reason: str | None = None,
        *,
        line_number: int | None = None,
        call_index: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.reason = reason if reason is not None else str(path)
        self.line_number = line_number
        self.call_index = call_index
        location = ""
        if line_number is not None:
            location += f" at line {line_number}"
        if call_index is not None:
            location += f" for call {call_index}"
        super().__init__(f"recording file {self.path}{location}: {self.reason}")


class ReplayMismatch(AssertionError):
    """An incoming request differs from what the recording file recorded."""

    def __init__(
        self,
        call_index: int,
        field_path: str,
        recorded: Any,
        incoming: Any,
        event_index: int | None = None,
    ) -> None:
        self.call_index = call_index
        self.field_path = field_path
        self.recorded = recorded
        self.incoming = incoming
        self.event_index = event_index
        position = f"call {call_index}"
        if event_index is not None:
            position += f", recorded event {event_index}"
        super().__init__(
            f"replay mismatch at {position}, field {field_path}: "
            f"recorded {recorded!r}, incoming {incoming!r}"
        )


@dataclass
class RecordedCall:
    call_index: int
    request: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)


def read_recording_file(recording_path: str | Path) -> list[RecordedCall]:
    """Parse and fully validate one supported JSONL recording."""
    path = Path(recording_path)
    try:
        restrict_recording_file(path)
        lines = []
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RecordingFileError(path, f"invalid JSON: {exc}", line_number=number) from exc
            if not isinstance(row, dict):
                raise RecordingFileError(path, "row must be an object", line_number=number)
            lines.append((number, row))
    except RecordingFileError:
        raise
    except OSError as exc:
        raise RecordingFileError(path, str(exc)) from exc
    if not lines or lines[0][1].get("type") != "header":
        raise RecordingFileError(path, "has no format header on its first line")
    recorded_version = lines[0][1].get("format_version")
    if recorded_version not in {1, RECORDING_FORMAT_VERSION}:
        raise RecordingFileError(
            path,
            f"was written in format version {recorded_version!r}; "
            f"this build replays version {RECORDING_FORMAT_VERSION}",
        )

    calls: dict[int, RecordedCall] = {}
    ended: set[int] = set()
    next_event: dict[int, int] = {}
    for number, line in lines[1:]:
        kind = line.get("type")
        if kind == "header":
            raise RecordingFileError(path, "duplicate header", line_number=number)
        if kind not in {"request", "event", "call_end"}:
            raise RecordingFileError(path, f"unknown row type {kind!r}", line_number=number)
        call_index = line.get("call_index")
        if not isinstance(call_index, int) or isinstance(call_index, bool) or call_index < 0:
            raise RecordingFileError(path, "invalid call_index", line_number=number)
        if kind == "request":
            if call_index != len(calls):
                raise RecordingFileError(path, "request call indexes must be contiguous", line_number=number)
            if call_index in calls:
                raise RecordingFileError(path, "duplicate request", line_number=number, call_index=call_index)
            options = line.get("options")
            if (
                recorded_version == 1
                and isinstance(options, dict)
                and options.get("response_format") is not None
            ):
                raise RecordingFileError(
                    path,
                    "format version 1 cannot replay structured response_format",
                    line_number=number,
                    call_index=call_index,
                )
            if recorded_version == 1 and isinstance(options, dict):
                # v1 persisted the request-local cancellation field as null;
                # v2 omits it because live asyncio.Event values are not JSON.
                options.pop("signal", None)
            calls[call_index] = RecordedCall(call_index=call_index, request=line)
            next_event[call_index] = 0
        elif kind == "event":
            if call_index not in calls:
                raise RecordingFileError(path, "orphan event", line_number=number, call_index=call_index)
            if call_index in ended:
                raise RecordingFileError(path, "event after call_end", line_number=number, call_index=call_index)
            event_index = line.get("event_index")
            if event_index != next_event[call_index]:
                raise RecordingFileError(path, "event indexes must be contiguous", line_number=number, call_index=call_index)
            payload = line.get("event")
            if not isinstance(payload, dict):
                raise RecordingFileError(path, "event payload must be an object", line_number=number, call_index=call_index)
            event_type = payload.get("type")
            event_class = _EVENT_CLASSES.get(event_type)
            if event_class is None:
                raise RecordingFileError(path, f"unknown event type {event_type!r}", line_number=number, call_index=call_index)
            try:
                event_class.model_validate(payload)
            except Exception as exc:
                raise RecordingFileError(path, f"invalid {event_type!r} event: {exc}", line_number=number, call_index=call_index) from exc
            calls[call_index].events.append(payload)
            next_event[call_index] += 1
        else:
            if call_index not in calls:
                raise RecordingFileError(path, "orphan call_end", line_number=number, call_index=call_index)
            if call_index in ended:
                raise RecordingFileError(path, "duplicate call_end", line_number=number, call_index=call_index)
            if line.get("event_count") != next_event[call_index]:
                raise RecordingFileError(path, "call_end event_count mismatch", line_number=number, call_index=call_index)
            ended.add(call_index)
    missing = sorted(set(calls) - ended)
    if missing:
        raise RecordingFileError(path, "missing call_end", call_index=missing[0])
    return [calls[index] for index in range(len(calls))]


# Wall-clock fields differ between the recording run and every replay run, so
# comparing them would make every recording file mismatch on the second message.
NON_DETERMINISTIC_FIELD_NAMES = frozenset({"timestamp"})


def find_first_difference(recorded: Any, incoming: Any, path: str = "") -> tuple[str, Any, Any] | None:
    """Return ``(field_path, recorded, incoming)`` for the first difference, or None.

    Walks dicts by sorted key and lists by index so the reported path is
    deterministic for the same pair of values. Fields in
    :data:`NON_DETERMINISTIC_FIELD_NAMES` are skipped.
    """
    if isinstance(recorded, dict) and isinstance(incoming, dict):
        for key in sorted(set(recorded) | set(incoming)):
            if key in NON_DETERMINISTIC_FIELD_NAMES:
                continue
            child = f"{path}.{key}" if path else key
            if key not in recorded:
                return (child, None, incoming[key])
            if key not in incoming:
                return (child, recorded[key], None)
            difference = find_first_difference(recorded[key], incoming[key], child)
            if difference is not None:
                return difference
        return None
    if isinstance(recorded, list) and isinstance(incoming, list):
        for index in range(max(len(recorded), len(incoming))):
            child = f"{path}[{index}]"
            if index >= len(recorded):
                return (child, None, incoming[index])
            if index >= len(incoming):
                return (child, recorded[index], None)
            difference = find_first_difference(recorded[index], incoming[index], child)
            if difference is not None:
                return difference
        return None
    if recorded != incoming:
        return (path or "<root>", recorded, incoming)
    return None


class ReplayProvider:
    """Serve recorded events for each call, never reaching the network.

    Register it in place of the real provider::

        register_api_provider(api, ReplayProvider(recording_path))
    """

    def __init__(self, recording_path: str | Path) -> None:
        self._calls = read_recording_file(recording_path)
        self._recording_path = Path(recording_path)
        self.call_count = 0

    @property
    def recording_path(self) -> Path:
        return self._recording_path

    @property
    def requires_credentials(self) -> bool:
        return False

    def assert_consumed(self) -> None:
        """Raise when the replay session ended before all calls were used."""
        if self.call_count != len(self._calls):
            remaining = len(self._calls) - self.call_count
            raise ReplayMismatch(
                self.call_count,
                "remaining_calls",
                recorded="all recorded calls consumed",
                incoming=f"{remaining} unconsumed call(s)",
            )

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self.stream_simple(model, context, options)

    async def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        call_index = self.call_count
        if call_index >= len(self._calls):
            raise ReplayMismatch(
                call_index,
                "call_index",
                recorded=f"{len(self._calls)} recorded calls",
                incoming=f"call {call_index}",
            )
        recorded = self._calls[call_index]
        self.call_count += 1
        self._check_request(recorded, model, context, options)

        for event_index, payload in enumerate(recorded.events):
            event_type = payload.get("type")
            event_class = _EVENT_CLASSES.get(event_type)
            if event_class is None:
                raise RecordingFileError(
                    f"recording file {self._recording_path} has unknown event type {event_type!r} "
                    f"at call {call_index}, event {event_index}"
                )
            yield event_class.model_validate(payload)

    def _check_request(
        self,
        recorded: RecordedCall,
        model: Model,
        context: Context,
        options: Any,
    ) -> None:
        incoming = {
            "model": remove_secret_values(model.model_dump(mode="json")),
            "context": remove_secret_values(context.model_dump(mode="json")),
            "options": _dump_options(options),
        }
        for part in ("model", "context", "options"):
            difference = find_first_difference(
                recorded.request.get(part), incoming[part], part
            )
            if difference is not None:
                field_path, recorded_value, incoming_value = difference
                raise ReplayMismatch(
                    recorded.call_index, field_path, recorded_value, incoming_value
                )
