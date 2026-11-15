"""Shared tool-execution wrappers used by ``@function`` and Agent methods.

Lives under ``programs/`` so ``agentic_programming`` can import it without
``programs`` depending on the agentic package (avoids the cycle).

What this module owns
---------------------
* A tool's return value as an ``AgentToolResult`` (``_normalize_result``),
  with the smaller cap a tool may declare recorded in its details; the
  agent loop bounds the result once (``spill.bound_text``).
* The whole-text file a bounded result names (``_persist_full_result``).
* ``asyncio.wait_for`` + sync-in-executor with copied Context
  (``invoke_callable``).
* The timeout ``AgentToolResult`` text (``timeout_tool_result``).
* Dispatch-layer sandbox / URL fail-closed
  (``dispatch_sandbox_error``), called before the tool body runs.

What stays local, and why
-------------------------
* Job-budget overlay (``current_job_operation_timeout``), LLM-clamped
  ``timeout_min``/``timeout_max``, ``on_update`` tail buffer, and
  ``Exception → is_error AgentToolResult`` live only on ``@function``.
  Agent methods have none of these.
* Cancel delivery differs: ``@function`` injects an ``asyncio.Event``
  kwarg; Agent methods bind ``_current_cancel``. Same Event
  object, two plumbing paths — do not unify.
* ``CancelledError → status=cancelled`` DAG write lives only in
  Agent methods sync/async wrappers. ``@function`` has no
  DAG node; it re-raises ``asyncio.CancelledError``.
* On timeout, Agent methods also call ``cancel.set()`` and update
  the DAG node. ``@function`` only returns an is_error result (plus
  job ``reason_code``). Those side effects stay on the agentic side.
* No-timeout sync invoke: ``@function`` always uses an executor;
  Agent methods still run on the event-loop thread. Pass
  ``run_sync_in_executor=`` to preserve each.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from openprogram.models.providers.types import AgentToolResult
from openprogram.models.providers.types import AudioContent, ImageContent, TextContent
from openprogram.state.store.session.tool_output_gc import TOOL_RESULTS_DIRNAME
import logging

_log = logging.getLogger(__name__)


# Characters the streaming ``on_update`` tail keeps when the tool names no
# capacity of its own: about one bounded result of English text.
DEFAULT_STREAM_CAPACITY_CHARS = 40_000
MIN_STREAM_CAPACITY_CHARS = 2_000

# ``AgentToolResult.details`` key of the cap a tool declared
# (``max_result_weight``): the agent loop bounds the result to the smaller
# of it and the model's cap (``spill.result_cap``).
CAP_DETAIL = "cap"

# Surface-path names the undeclared-tool fallback will inspect.
_FALLBACK_PATH_KEYS = frozenset({"path", "file_path"})
_READ_NAME_HINTS = ("read", "list", "glob", "grep", "pdf")
_WRITE_NAME_HINTS = ("write", "edit")


@dataclass
class ToolReturn:
    """Optional structured return value. Tools can also return a plain
    str (auto-wrapped as TextContent) or an AgentToolResult directly.
    """
    text: Optional[str] = None
    images: list[Union[bytes, str]] = field(default_factory=list)
    # WAV recordings; providers whose model accepts audio input pass them on.
    audio: list[bytes] = field(default_factory=list)
    json_data: Any = None
    is_error: bool = False


def _tool_results_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    p = get_state_dir() / TOOL_RESULTS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_results_dir() -> Path | None:
    """``<session>/tool_results/`` of the conversation the running tool
    belongs to, or None when no conversation is running or its directory
    does not exist (a one-off CLI call)."""
    try:
        from openprogram.runtime.execution.run_control import get_current_session_id
        from openprogram.runtime.context.session_db import default_db

        session_id = get_current_session_id()
        if not session_id:
            return None
        session_dir = Path(default_db()._session_dir(session_id))  # noqa: SLF001
    except Exception:  # noqa: BLE001 - fall back to the shared directory
        _log.debug("no session directory for a full tool result", exc_info=True)
        return None
    if not session_dir.is_dir():
        return None
    return session_dir / TOOL_RESULTS_DIRNAME


def _persist_full_result(
    call_id: str, text: str, *, results_dir: Path | None = None,
    fallback_dir: Callable[[], Path] | None = None,
) -> Path:
    """Keep a tool result's whole text and return its path.

    The file is named by the text's content (``<sha256>.txt``) inside the
    running conversation's directory, so it is deleted and backed up with
    the conversation, the same text is kept once, and two texts never take
    one name whatever call made them. ``results_dir`` names the directory
    outright; without a conversation the file goes to ``fallback_dir()``
    (the profile's shared ``tool_results/``). An existing file is reused:
    its name says it holds this text. ``call_id`` is kept for callers.
    """
    import hashlib
    import os
    import tempfile

    from openprogram.host._platform.filesystem import replace_file

    del call_id
    directory = results_dir if results_dir is not None else _session_results_dir()
    if directory is None:
        directory = (fallback_dir or _tool_results_dir)()
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:32]
    path = directory / f"{digest}.txt"
    if path.is_file():
        return path
    fd, name = tempfile.mkstemp(prefix=f".{digest}-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        # Same name, same text: a writer that got here first left the
        # same bytes, so replacing its file changes nothing.
        replace_file(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def with_cap(result: AgentToolResult, cap: int | None) -> AgentToolResult:
    """``result`` with the tool's own cap recorded in its details."""
    if not cap:
        return result
    details = dict(result.details) if isinstance(result.details, dict) else {}
    details[CAP_DETAIL] = int(cap)
    return result.model_copy(update={"details": details})


def _normalize_result(
    raw: Any,
    *,
    call_id: str,
    cap: int | None = None,
) -> AgentToolResult:
    """Convert a tool's raw return value into an AgentToolResult.

    Accepted shapes:
      - str → TextContent ("Error: ..." also sets is_error)
      - dict / list → JSON-serialized as TextContent
      - ToolReturn → text + images + json
      - AgentToolResult → passthrough

    Nothing is cut here: the agent loop bounds every result once, where
    it is stored (``tool_dispatch._as_stored``). ``cap``, the smaller
    weight a tool may declare (``max_result_weight``), is recorded in the
    details for that bound.
    """
    del call_id
    if isinstance(raw, AgentToolResult):
        return with_cap(raw, cap)

    images: list[ImageContent] = []
    audio: list[AudioContent] = []
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
                images.append(ImageContent(data=b64, mime_type="image/png"))
            elif isinstance(img, str):
                images.append(ImageContent(data=img, mime_type="image/png"))
        import base64
        audio = [AudioContent(data=base64.b64encode(clip).decode("ascii"), mime_type="audio/wav")
                 for clip in raw.audio if clip]
    elif isinstance(raw, str):
        text_part = raw
        # Tools report a failure as an "Error: ..." string; mark it so the
        # transcript and repeat-failure accounting see it as one.
        is_error = raw.startswith("Error: ")
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
        except (TypeError, ValueError, RecursionError):
            # Not serialisable even with default=str (non-str keys, cycles); text stays empty.
            _log.debug("tool result json payload not serialisable", exc_info=True)

    content: list[Any] = []
    if text_part:
        content.append(TextContent(text=text_part))
    content.extend(images)
    content.extend(audio)
    if not content:
        content.append(TextContent(text=""))

    details: dict[str, Any] = {}
    if json_payload is not None:
        details["json"] = json_payload

    return with_cap(AgentToolResult(
        content=content,
        details=details or None,
        is_error=is_error,
    ), cap)


def _iter_str_values(value: Any):
    if isinstance(value, str):
        if value:
            yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item:
                yield item


def infer_path_direction(tool_name: str) -> str:
    """Read if the name is clearly a reader; otherwise write (fail-closed)."""
    lowered = tool_name.lower()
    if any(hint in lowered for hint in _WRITE_NAME_HINTS):
        return "write"
    if any(hint in lowered for hint in _READ_NAME_HINTS):
        return "read"
    return "write"


def resolve_path_params(
    tool_name: str,
    args: dict[str, Any],
    declared: dict[str, str] | None,
) -> dict[str, str]:
    """Declared map wins. ``{}`` is an explicit exemption. ``None`` → fallback."""
    if declared is not None:
        return declared
    direction = infer_path_direction(tool_name)
    return {
        key: direction
        for key in _FALLBACK_PATH_KEYS
        if key in args
    }


def _path_for_write_check(raw: str) -> str:
    """Match write/edit: relative paths bind to the active worktree first.

    ``validate_write_path`` realpaths a relative arg against process cwd
    but compares writable roots to the worktree. File tools call
    ``resolve_path`` before validating; dispatch does the same so a
    worktree-relative write is not rejected as outside the roots.
    """
    import os
    if os.path.isabs(raw):
        return raw
    try:
        from openprogram.state.worktree.path_resolve import resolve_path
        resolved, _ = resolve_path(raw)
        return resolved
    except Exception:
        return raw


def dispatch_sandbox_error(
    tool_name: str,
    args: dict[str, Any],
    *,
    path_params: dict[str, str] | None = None,
    url_params: list[str] | None = None,
) -> AgentToolResult | None:
    """Fail-closed path/URL check. Returns an is_error result, or None to proceed.

    Read and write paths are worktree-anchored first, same as the file
    tools (a relative path names a file in the bound worktree, not in
    the process cwd).
    """
    from openprogram.host.sandbox import validate_read_path, validate_write_path
    from openprogram.host.security.url_policy import URLPolicyError, normalize_url

    resolved = resolve_path_params(tool_name, args, path_params)
    for key, direction in resolved.items():
        if key not in args:
            continue
        for raw in _iter_str_values(args[key]):
            if direction == "read":
                # Relative reads bind to the worktree too: the tool reads
                # there, so the check must look at the same file.
                violation = validate_read_path(_path_for_write_check(raw))
            else:
                violation = validate_write_path(_path_for_write_check(raw))
            if violation:
                from openprogram.runtime.permissions import refusal
                return refusal.sandbox(tool_name, violation).result()

    for key in url_params or ():
        if key not in args:
            continue
        for raw in _iter_str_values(args[key]):
            try:
                normalize_url(raw)
            except URLPolicyError as exc:
                return AgentToolResult(
                    content=[TextContent(text=f"Error: {exc}")],
                    is_error=True,
                )
    return None


def timeout_tool_result(
    name: str,
    seconds: float,
    *,
    details: dict[str, Any] | None = None,
) -> AgentToolResult:
    """``is_error`` result for a ``wait_for`` timeout. Details stay caller-specific."""
    return AgentToolResult(
        content=[TextContent(text=(
            f"[error] function {name} timed out after {seconds}s"
        ))],
        details=details,
        is_error=True,
    )


async def invoke_callable(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    timeout: float | None = None,
    is_async: bool | None = None,
    run_sync_in_executor: bool = True,
) -> Any:
    """Run ``fn(**kwargs)`` under an optional ``asyncio.wait_for`` budget.

    Sync callables run in a worker thread with the current Context copied
    across when ``run_sync_in_executor`` is true *or* a timeout is set
    (``wait_for`` cannot interrupt a blocking loop thread). The
    Agent methods no-timeout path passes ``run_sync_in_executor=False``
    so the body still runs on the event-loop thread.

    Raises ``asyncio.TimeoutError`` and ``asyncio.CancelledError``
    unchanged. Does not wrap other exceptions.
    """
    if is_async is None:
        is_async = inspect.iscoroutinefunction(fn)

    if is_async:
        coro = fn(**kwargs)
        if timeout is not None:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    if timeout is not None or run_sync_in_executor:
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        work = loop.run_in_executor(None, lambda: ctx.run(fn, **kwargs))
        if timeout is not None:
            return await asyncio.wait_for(work, timeout=timeout)
        return await work

    raw = fn(**kwargs)
    if inspect.iscoroutine(raw):
        return await raw
    return raw
