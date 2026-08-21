"""Shared tool-execution wrappers used by ``@function`` and ``@agentic_function``.

Lives under ``programs/`` so ``agentic_programming`` can import it without
``programs`` depending on the agentic package (avoids the cycle).

What this module owns
---------------------
* Result cap + optional persist-to-disk (``_normalize_result``).
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
  ``@agentic_function`` has none of these.
* Cancel delivery differs: ``@function`` injects an ``asyncio.Event``
  kwarg; ``@agentic_function`` binds ``_current_cancel``. Same Event
  object, two plumbing paths — do not unify.
* ``CancelledError → status=cancelled`` DAG write lives only in
  ``@agentic_function``'s sync/async wrappers. ``@function`` has no
  DAG node; it re-raises ``asyncio.CancelledError``.
* On timeout, ``@agentic_function`` also ``cancel.set()`` and patches
  the DAG node. ``@function`` only returns an is_error result (plus
  job ``reason_code``). Those side effects stay on the agentic side.
* No-timeout sync invoke: ``@function`` always uses an executor;
  ``@agentic_function`` still runs on the event-loop thread. Pass
  ``run_sync_in_executor=`` to preserve each.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from openprogram.agent.types import AgentToolResult
from openprogram.providers.types import ImageContent, TextContent


DEFAULT_MAX_RESULT_CHARS = 30_000
MIN_KEEP_CHARS = 2_000
DEFAULT_HEAD_RATIO = 0.7
TOOL_RESULTS_DIRNAME = "tool_results"

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
    json_data: Any = None
    is_error: bool = False


def _tool_results_dir() -> Path:
    from openprogram.paths import get_state_dir
    p = get_state_dir() / TOOL_RESULTS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def _safe_result_id(call_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(call_id or ""))[:128]
    return cleaned or "result"


def _persist_full_result(
    call_id: str, text: str, *, results_dir: Path | None = None,
) -> Path:
    directory = results_dir if results_dir is not None else _tool_results_dir()
    p = directory / f"{_safe_result_id(call_id)}.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _normalize_result(
    raw: Any,
    *,
    call_id: str,
    max_chars: int,
    persist_full: bool,
    head_ratio: float,
    persist: Callable[[str, str], Path] | None = None,
) -> AgentToolResult:
    """Convert a tool's raw return value into an AgentToolResult.

    Accepted shapes:
      - str → TextContent
      - dict / list → JSON-serialized as TextContent
      - ToolReturn → text + images + json
      - AgentToolResult → passthrough (no cap)

    Then applies char cap with optional persist-to-disk for the full
    version (so the LLM can lazy-load via a read tool when needed).
    ``persist`` overrides the default writer so callers that monkeypatch
    ``_runtime._tool_results_dir`` keep working.
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
                images.append(ImageContent(data=b64, mime_type="image/png"))
            elif isinstance(img, str):
                images.append(ImageContent(data=img, mime_type="image/png"))
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

    full_text = text_part
    if len(full_text) > max_chars:
        if persist_full:
            try:
                writer = persist or _persist_full_result
                p = writer(call_id, full_text)
                marker = (
                    f"\n\n[Full result ({len(full_text):,} chars) saved at "
                    f"{p} — read tool can fetch it]"
                )
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
    if json_payload is not None:
        details["json"] = json_payload

    return AgentToolResult(
        content=content,
        details=details or None,
        is_error=is_error,
    )


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
        from openprogram.worktree.path_resolve import resolve_path
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

    Read paths go to ``validate_read_path`` unchanged (it already
    ``expanduser`` + ``realpath``s; relative → process cwd). Write
    paths are worktree-anchored first, same as the file tools.
    """
    from openprogram.sandbox import validate_read_path, validate_write_path
    from openprogram.security.url_policy import URLPolicyError, normalize_url

    resolved = resolve_path_params(tool_name, args, path_params)
    for key, direction in resolved.items():
        if key not in args:
            continue
        for raw in _iter_str_values(args[key]):
            if direction == "read":
                violation = validate_read_path(raw)
            else:
                violation = validate_write_path(_path_for_write_check(raw))
            if violation:
                return AgentToolResult(
                    content=[TextContent(text=f"Error: sandbox policy: {violation}")],
                    is_error=True,
                )

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
    ``@agentic_function`` no-timeout path passes ``run_sync_in_executor=False``
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
