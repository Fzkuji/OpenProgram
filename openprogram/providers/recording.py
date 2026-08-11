"""Record provider calls to a JSONL recording file.

A recorder wraps any registered :class:`~openprogram.providers.api_registry.ApiProvider`
and writes one JSON object per line: the request (model, context, options) and
every streamed event, in the order the real provider produced them. The recording file is
what :mod:`openprogram.providers.replay` reads back.

Redaction is unconditional. Every value written passes through
:func:`remove_secret_values` first, so an ``Authorization`` header, an API key,
a token or a cookie never reaches the file in plain text — there is no option
to turn it off.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator

from openprogram import _compat
from openprogram._compat import restrict_to_user

from .types import (
    AssistantMessageEvent,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

# Bump when the line shape changes. replay.py refuses a recording file whose header
# version differs from this value.
RECORDING_FORMAT_VERSION = 1

PLACEHOLDER = "[secret removed]"

# Field names whose value is a secret wherever it appears — request options,
# model headers, nested provider-specific dicts.
SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-goog-api-key",
    "api-key",
    "secret",
    "client_secret",
    "password",
    "session_key",
})

# Secret-looking values that survive field-name matching, e.g. a bearer token
# pasted into a free-form string field or a URL query parameter.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic|bot|token|key)\s+[\w.:\-~+/]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s]+"),
    re.compile(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@"),
)


def remove_secret_values(value: Any) -> Any:
    """Return ``value`` with every secret field and secret-looking string replaced.

    Recurses through dicts and lists. Dict keys are matched case-insensitively
    against :data:`SECRET_FIELD_NAMES`; matching keys lose their whole value.
    Remaining strings are scanned for bearer tokens, ``sk-`` keys and secrets
    carried in URL query parameters.
    """
    if isinstance(value, dict):
        return {
            key: PLACEHOLDER if str(key).lower() in SECRET_FIELD_NAMES
            else remove_secret_values(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [remove_secret_values(item) for item in value]
    if isinstance(value, str):
        cleaned = value
        for pattern in _SECRET_VALUE_PATTERNS:
            cleaned = pattern.sub(
                lambda match: (
                    match.group(1) + PLACEHOLDER + "@"
                    if match.groups() and match.group(1).lower().startswith("http")
                    else match.group(1) + PLACEHOLDER
                    if match.groups()
                    else PLACEHOLDER
                ),
                cleaned,
            )
        return cleaned
    return value


def _dump(model_or_none: Any) -> Any:
    if model_or_none is None:
        return None
    return remove_secret_values(model_or_none.model_dump(mode="json"))


class RecordingProvider:
    """Delegate to a real provider and append every call to a JSONL recording file.

    Register it in place of the provider it wraps::

        register_api_provider(api, RecordingProvider(real_provider, recording_path))
    """

    def __init__(self, provider: Any, recording_path: str | Path | RecordingSink) -> None:
        self._provider = provider
        self._sink = (
            recording_path if isinstance(recording_path, RecordingSink) else RecordingSink(recording_path)
        )
        self._recording_path = self._sink.path

    @property
    def recording_path(self) -> Path:
        return self._recording_path

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self._record(self._provider.stream(model, context, options), model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self._record(
            self._provider.stream_simple(model, context, options), model, context, options
        )

    async def _record(
        self,
        source: AsyncGenerator[AssistantMessageEvent, None],
        model: Model,
        context: Context,
        options: Any,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        call_index = self._sink.begin_call({
            "model": _dump(model),
            "context": _dump(context),
            "options": _dump(options),
        })
        event_index = 0
        ended = False
        async for event in source:
            self._sink.append_event(call_index, event_index, _dump(event))
            event_index += 1
            if getattr(event, "type", None) in {"done", "error"}:
                self._sink.end_call(call_index, event_index)
                ended = True
            yield event
        if not ended:
            self._sink.end_call(call_index, event_index)


class RecordingSink:
    """Cross-thread and cross-process append coordinator for one JSONL file."""

    def __init__(self, recording_path: str | Path) -> None:
        self.path = Path(recording_path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._thread_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self._locked():
            self._ensure_header_locked()

    def begin_call(self, request: dict[str, Any]) -> int:
        with self._locked():
            call_indexes = []
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if row.get("type") == "request":
                    call_indexes.append(row["call_index"])
            call_index = max(call_indexes, default=-1) + 1
            self._append_locked({
                "type": "request",
                "call_index": call_index,
                **remove_secret_values(request),
            })
            return call_index

    def append_event(self, call_index: int, event_index: int, event: dict[str, Any]) -> None:
        with self._locked():
            self._append_locked({
                "type": "event",
                "call_index": call_index,
                "event_index": event_index,
                "event": remove_secret_values(event),
            })

    def end_call(self, call_index: int, event_count: int) -> None:
        with self._locked():
            self._append_locked({
                "type": "call_end",
                "call_index": call_index,
                "event_count": event_count,
            }, fsync=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            lock_fd = os.open(self.lock_path, flags, 0o600)
            try:
                restrict_to_user(self.lock_path)
                _verify_private_regular_file(lock_fd, self.lock_path)
                _compat.flock(lock_fd, _compat.LOCK_EX)
                yield
            finally:
                _compat.flock(lock_fd, _compat.LOCK_UN)
                os.close(lock_fd)

    def _ensure_header_locked(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            restrict_to_user(self.path)
            _verify_private_regular_file(fd, self.path)
            if os.fstat(fd).st_size == 0:
                _write_all(fd, _encode_line({
                    "type": "header",
                    "format_version": RECORDING_FORMAT_VERSION,
                }))
        finally:
            os.close(fd)

    def _append_locked(self, row: dict[str, Any], *, fsync: bool = False) -> None:
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags)
        try:
            _verify_private_regular_file(fd, self.path)
            _write_all(fd, _encode_line(row))
            if fsync:
                os.fsync(fd)
        finally:
            os.close(fd)


def _encode_line(line: dict[str, Any]) -> bytes:
    return (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("recording write made no progress")
        view = view[written:]


def _verify_private_regular_file(fd: int, path: Path) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"recording path is not a regular file: {path}")
    if sys.platform != "win32" and stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError(f"recording file must have mode 0600: {path}")


def restrict_recording_file(path: str | Path) -> None:
    """Tighten and verify a recording before it is read."""
    recording_path = Path(path)
    if recording_path.is_symlink():
        raise PermissionError(f"recording path must not be a symlink: {recording_path}")
    restrict_to_user(recording_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(recording_path, flags)
    try:
        _verify_private_regular_file(fd, recording_path)
    finally:
        os.close(fd)


def resolve_recording_selector(selector: str) -> Path:
    """Resolve a managed ID or explicit filesystem selector."""
    value = selector.strip()
    if not value:
        raise ValueError("recording file selector is required")
    if Path(value).is_absolute() or "/" in value or "\\" in value:
        return Path(value).expanduser().resolve()
    from openprogram.paths import get_recordings_dir
    filename = value if value.endswith(".jsonl") else f"{value}.jsonl"
    return get_recordings_dir() / filename


def activate_record_replay_from_config() -> None:
    """Install the configured process-wide mode after built-ins register."""
    from openprogram import setup
    from openprogram.providers.api_registry import configure_provider_transform

    config = setup._read_config().get("record_replay", {})
    mode = config.get("mode", "off")
    if mode == "off":
        return
    selector = config.get("file", "")
    path = resolve_recording_selector(selector)
    if mode == "record":
        if Path(selector).is_absolute() or "/" in selector or "\\" in selector:
            raise ValueError("record mode requires a managed recording ID")
        sink = RecordingSink(path)
        configure_provider_transform(
            lambda api, provider: RecordingProvider(provider, sink)
        )
        return
    if mode == "replay":
        from openprogram.providers.replay import ReplayProvider

        replay = ReplayProvider(path)
        configure_provider_transform(lambda api, provider: replay)
        return
    raise ValueError(f"unsupported record_replay.mode: {mode!r}")
