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

import asyncio
import json
import logging
import os
import re
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Iterator
import secrets

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
REDACTION_VERSION = 1

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
_SECRET_FIELD_SUFFIX = re.compile(r"(?:^|[-_])(?:token|key|secret|password)$", re.IGNORECASE)

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
            key: PLACEHOLDER if _is_secret_field_name(key)
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


def _is_secret_field_name(value: Any) -> bool:
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(value)).lower()
    return name in SECRET_FIELD_NAMES or _SECRET_FIELD_SUFFIX.search(name) is not None


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

    @property
    def requires_credentials(self) -> bool:
        return getattr(self._provider, "requires_credentials", True)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self._record(
            lambda: self._provider.stream(model, context, options),
            model,
            context,
            options,
        )

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self._record(
            lambda: self._provider.stream_simple(model, context, options),
            model,
            context,
            options,
        )

    async def _record(
        self,
        source_factory: Callable[[], AsyncGenerator[AssistantMessageEvent, None]],
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
        outcome = "complete"
        primary_error: BaseException | None = None
        primary_traceback = None
        cleanup_error: BaseException | None = None
        source: AsyncGenerator[AssistantMessageEvent, None] | None = None
        try:
            source = source_factory()
            async for event in source:
                self._sink.append_event(call_index, event_index, _dump(event))
                event_index += 1
                if getattr(event, "type", None) in {"done", "error"}:
                    self._sink.end_call(call_index, event_index)
                    ended = True
                yield event
                if ended:
                    break
        except BaseException as exc:
            primary_error = exc
            primary_traceback = exc.__traceback__
            if isinstance(exc, asyncio.CancelledError):
                outcome = "cancelled"
            elif isinstance(exc, GeneratorExit):
                outcome = "abandoned"
            else:
                outcome = "error"
        finally:
            if not ended:
                try:
                    self._sink.end_call(call_index, event_index, outcome=outcome)
                except BaseException as exc:
                    cleanup_error = exc
            if source is not None:
                try:
                    await source.aclose()
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
        if primary_error is not None:
            raise primary_error.with_traceback(primary_traceback)
        if cleanup_error is not None:
            raise cleanup_error


class RecordingSink:
    """Cross-thread and cross-process append coordinator for one JSONL file."""

    def __init__(self, recording_path: str | Path) -> None:
        self.path = Path(recording_path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._thread_lock = threading.RLock()
        from openprogram.paths import get_state_dir
        managed_root = get_state_dir() / "recordings"
        try:
            inside_managed_root = self.path.absolute().is_relative_to(
                managed_root.absolute()
            )
        except OSError:
            inside_managed_root = False
        if inside_managed_root and managed_root.is_symlink():
            raise PermissionError(
                f"recordings directory must not be a symlink: {managed_root}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform != "win32" and _is_managed_recording_path(self.path.parent):
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

    def end_call(
        self,
        call_index: int,
        event_count: int,
        *,
        outcome: str = "complete",
    ) -> None:
        with self._locked():
            self._append_locked({
                "type": "call_end",
                "call_index": call_index,
                "event_count": event_count,
                "outcome": outcome,
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
    if _is_managed_recording_path(recording_path):
        restrict_to_user(recording_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(recording_path, flags)
    try:
        _verify_private_regular_file(fd, recording_path)
    finally:
        os.close(fd)


def _is_managed_recording_path(path: Path) -> bool:
    from openprogram.paths import get_state_dir

    managed_root = (get_state_dir() / "recordings").resolve()
    try:
        path.resolve().relative_to(managed_root)
    except ValueError:
        return False
    return True


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


def activate_record_replay_from_config() -> str:
    """Install the configured process-wide mode after built-ins register."""
    from openprogram import setup
    from openprogram.providers.api_registry import configure_provider_transform

    config = setup._read_config().get("record_replay", {})
    mode = config.get("mode", "off")
    if mode == "off":
        return mode
    selector = config.get("file", "")
    path = resolve_recording_selector(selector)
    if mode == "record":
        if Path(selector).is_absolute() or "/" in selector or "\\" in selector:
            raise ValueError("record mode requires a managed recording ID")
        sink = RecordingSink(path)
        configure_provider_transform(
            lambda api, provider: RecordingProvider(provider, sink)
        )
        return mode
    if mode == "replay":
        from openprogram.providers.replay import ReplayProvider

        replay = ReplayProvider(path)
        configure_provider_transform(lambda api, provider: replay)
        return mode
    raise ValueError(f"unsupported record_replay.mode: {mode!r}")


class _BlockedRecordReplayProvider:
    """Fail closed when configured recording or replay cannot be activated."""

    requires_credentials = False

    async def stream(self, model, context, options=None):
        raise RuntimeError(
            "record/replay configuration is invalid; run "
            "`openprogram recordings status` or `openprogram recordings off`"
        )
        yield  # pragma: no cover - keeps this method an async generator

    def stream_simple(self, model, context, options=None):
        return self.stream(model, context, options)


def activate_record_replay_safely() -> str:
    """Activate startup configuration without enabling a live-provider fallback.

    Returns the resulting mode: ``off``/``record``/``replay`` on success, or
    ``blocked`` when configuration is invalid — in which case a fail-closed
    provider is installed so every provider call raises the same diagnostic
    instead of silently reaching the network.
    """
    try:
        return activate_record_replay_from_config()
    except Exception as exc:
        logging.getLogger("openprogram.providers").error(
            "record/replay activation failed; provider calls are blocked (%s)",
            type(exc).__name__,
        )
        from openprogram.providers.api_registry import _replace_provider_transform

        blocked = _BlockedRecordReplayProvider()
        _replace_provider_transform(lambda api, provider: blocked)
        return "blocked"


class RecordingManagementError(RuntimeError):
    """A managed recording operation is unsafe or cannot be completed."""


@dataclass(frozen=True)
class RecordingInfo:
    recording_id: str
    path: Path
    created_at: str
    modified_at: float
    size: int
    call_count: int
    valid: bool
    active: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


_RECORDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _validated_recording_id(name: str | None) -> str:
    recording_id = name or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    )
    if recording_id.endswith(".jsonl"):
        recording_id = recording_id[:-6]
    if not _RECORDING_ID.fullmatch(recording_id) or recording_id in {".", ".."}:
        raise RecordingManagementError(
            "recording ID must contain only letters, digits, dot, underscore, or hyphen"
        )
    return recording_id


def _managed_path(recording_id: str) -> Path:
    from openprogram.paths import get_recordings_dir

    clean_id = _validated_recording_id(recording_id)
    return get_recordings_dir() / f"{clean_id}.jsonl"


def create_managed_recording(name: str | None = None) -> RecordingInfo:
    """Create a new header-only managed recording without overwriting."""
    recording_id = _validated_recording_id(name)
    path = _managed_path(recording_id)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fd = os.open(path, flags, 0o600)
    try:
        try:
            restrict_to_user(path)
            _verify_private_regular_file(fd, path)
            _write_all(fd, _encode_line({
                "type": "header",
                "format_version": RECORDING_FORMAT_VERSION,
                "recording_id": recording_id,
                "created_at": created_at,
                "redaction_version": REDACTION_VERSION,
            }))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    info = path.stat()
    return RecordingInfo(
        recording_id=recording_id,
        path=path,
        created_at=created_at,
        modified_at=info.st_mtime,
        size=info.st_size,
        call_count=0,
        valid=True,
    )


def _update_mode(mode: str, selector: str | None = None) -> dict[str, Any]:
    from openprogram import setup

    def mutate(config: dict[str, Any]) -> None:
        setting = config.setdefault("record_replay", {})
        setting["mode"] = mode
        if selector is not None:
            setting["file"] = selector

    return setup.update_config(mutate)


def set_record_mode(name: str | None = None) -> RecordingInfo:
    info = create_managed_recording(name)
    try:
        _update_mode("record", info.recording_id)
    except Exception:
        try:
            info.path.unlink()
        except OSError:
            pass
        raise
    return info


def set_replay_mode(selector: str) -> RecordingInfo:
    path = resolve_recording_selector(selector)
    read_recording_file = _read_recording_file()
    read_recording_file(path)
    info = _recording_info(path)
    stored_selector = info.recording_id if path.parent == _managed_root_resolved() else str(path)
    _update_mode("replay", stored_selector)
    return info


def set_record_replay_off() -> None:
    _update_mode("off")


def _read_recording_file():
    from openprogram.providers.replay import read_recording_file

    return read_recording_file


def _managed_root_resolved() -> Path:
    from openprogram.paths import get_recordings_dir

    return get_recordings_dir().resolve()


def _active_managed_id() -> str | None:
    from openprogram import setup

    setting = setup._read_config().get("record_replay", {})
    if setting.get("mode") not in {"record", "replay"}:
        return None
    selector = setting.get("file", "")
    if not selector or Path(selector).is_absolute() or "/" in selector or "\\" in selector:
        return None
    try:
        return _validated_recording_id(selector)
    except RecordingManagementError:
        return None


def _recording_info(path: Path) -> RecordingInfo:
    recording_id = path.name[:-6] if path.name.endswith(".jsonl") else path.name
    if path.is_symlink():
        return RecordingInfo(recording_id, path, "", 0, 0, 0, False, error="symlink")
    info = path.stat()
    try:
        calls = _read_recording_file()(path)
        header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        return RecordingInfo(
            recording_id=recording_id,
            path=path,
            created_at=header.get(
                "created_at",
                datetime.fromtimestamp(info.st_ctime, timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
            modified_at=info.st_mtime,
            size=info.st_size,
            call_count=len(calls),
            valid=True,
            active=recording_id == _active_managed_id(),
        )
    except Exception as exc:
        return RecordingInfo(
            recording_id, path, "", info.st_mtime, info.st_size, 0, False,
            active=recording_id == _active_managed_id(), error=str(exc),
        )


def list_recordings() -> list[RecordingInfo]:
    root = _managed_root_resolved()
    return sorted(
        (_recording_info(path) for path in root.iterdir() if path.name.endswith(".jsonl")),
        key=lambda item: (item.modified_at, item.recording_id),
        reverse=True,
    )


def show_recording(selector: str, *, include_content: bool = False) -> dict[str, Any]:
    path = resolve_recording_selector(selector)
    info = _recording_info(path)
    result = info.to_dict()
    if include_content:
        restrict_recording_file(path)
        result["content"] = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return result


@contextmanager
def _management_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        restrict_to_user(lock_path)
        _verify_private_regular_file(fd, lock_path)
        try:
            _compat.flock(fd, _compat.LOCK_EX | _compat.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise RecordingManagementError(f"recording is locked: {path.name}") from exc
        yield
    finally:
        _compat.flock(fd, _compat.LOCK_UN)
        os.close(fd)


def delete_recording(recording_id: str) -> int:
    if Path(recording_id).is_absolute() or "/" in recording_id or "\\" in recording_id:
        raise RecordingManagementError("delete requires a managed ID")
    clean_id = _validated_recording_id(recording_id)
    path = _managed_path(clean_id)
    if path.is_symlink():
        raise RecordingManagementError("refusing to delete a symlink")
    if clean_id == _active_managed_id():
        raise RecordingManagementError("refusing to delete the active recording")
    if not path.is_file():
        raise RecordingManagementError(f"recording does not exist: {clean_id}")
    size = path.stat().st_size
    lock_path = path.with_name(path.name + ".lock")
    with _management_lock(path):
        path.unlink()
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    return size


def prune_recordings(older_than_days: int, *, dry_run: bool = False) -> dict[str, Any]:
    if isinstance(older_than_days, bool) or older_than_days <= 0:
        raise RecordingManagementError("older-than-days must be a positive integer")
    cutoff = time.time() - older_than_days * 86400
    result: dict[str, Any] = {"matched": 0, "deleted": 0, "failed": 0, "bytes": 0, "recordings": []}
    active = _active_managed_id()
    for info in list_recordings():
        if info.recording_id == active or info.modified_at >= cutoff or not info.valid:
            continue
        result["matched"] += 1
        result["recordings"].append(info.recording_id)
        if dry_run:
            continue
        try:
            result["bytes"] += delete_recording(info.recording_id)
            result["deleted"] += 1
        except RecordingManagementError:
            result["failed"] += 1
    return result


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _confirmed(action: str, target: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"error: {action} requires --yes when stdin is not a TTY", file=sys.stderr)
        return False
    return input(f"Type {target} to confirm {action}: ").strip() == target


def dispatch_recordings(args: Any) -> int:
    """Execute one parsed `openprogram recordings` command."""
    try:
        verb = args.recordings_verb
        if verb == "status":
            from openprogram import setup
            setting = setup._read_config().get("record_replay", {})
            payload = {"mode": setting.get("mode", "off"), "file": setting.get("file", "")}
            _print_json(payload) if args.json else print(f"mode: {payload['mode']}\nfile: {payload['file'] or '-'}")
        elif verb == "record":
            info = set_record_mode(args.name)
            print(f"recording {info.recording_id}; takes effect next start")
        elif verb == "replay":
            info = set_replay_mode(args.selector)
            print(f"replaying {info.recording_id}; takes effect next start")
        elif verb == "off":
            set_record_replay_off()
            print("record/replay disabled; takes effect next start")
        elif verb == "list":
            rows = [item.to_dict() for item in list_recordings()]
            _print_json(rows) if args.json else [print(item["recording_id"]) for item in rows]
        elif verb == "show":
            payload = show_recording(args.selector, include_content=args.content)
            _print_json(payload) if args.json else print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        elif verb == "delete":
            if not _confirmed("delete", args.recording_id, args.yes):
                return 2
            delete_recording(args.recording_id)
            print(f"deleted {args.recording_id}")
        elif verb == "prune":
            if not args.dry_run and not _confirmed("prune", "prune", args.yes):
                return 2
            payload = prune_recordings(args.older_than_days, dry_run=args.dry_run)
            _print_json(payload)
        else:
            args._cmd_parser.print_help()
            return 2
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
