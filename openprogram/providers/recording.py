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
from pathlib import Path
from typing import Any, AsyncGenerator

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

    def __init__(self, provider: Any, recording_path: str | Path) -> None:
        self._provider = provider
        self._recording_path = Path(recording_path)
        self._call_index = 0
        self._start_recording_file()

    @property
    def recording_path(self) -> Path:
        return self._recording_path

    def _start_recording_file(self) -> None:
        self._recording_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._recording_path.parent, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._recording_path, flags, 0o600)
        try:
            restrict_to_user(self._recording_path)
            _verify_private_regular_file(fd, self._recording_path)
            if os.fstat(fd).st_size == 0:
                _write_all(fd, _encode_line({
                    "type": "header",
                    "format_version": RECORDING_FORMAT_VERSION,
                }))
        finally:
            os.close(fd)

    def _write(self, line: dict[str, Any]) -> None:
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._recording_path, flags)
        try:
            _verify_private_regular_file(fd, self._recording_path)
            _write_all(fd, _encode_line(line))
        finally:
            os.close(fd)

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
        call_index = self._call_index
        self._call_index += 1
        self._write({
            "type": "request",
            "call_index": call_index,
            "model": _dump(model),
            "context": _dump(context),
            "options": _dump(options),
        })
        event_index = 0
        ended = False
        async for event in source:
            self._write({
                "type": "event",
                "call_index": call_index,
                "event_index": event_index,
                "event": _dump(event),
            })
            event_index += 1
            if getattr(event, "type", None) in {"done", "error"}:
                self._write({
                    "type": "call_end",
                    "call_index": call_index,
                    "event_count": event_index,
                })
                ended = True
            yield event
        if not ended:
            self._write({
                "type": "call_end",
                "call_index": call_index,
                "event_count": event_index,
            })


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
