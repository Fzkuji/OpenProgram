"""Test helpers for program packages.

``script_roles`` replaces the model behind each role with scripted replies
and records every request, built by the same code path a real call uses::

    from openprogram.programs._authoring.testing import script_roles

    with script_roles({program.writer: ["Draft"], program.reviewer: ["yes"]}) as calls:
        program.weekly_report("notes")

    request = calls[program.writer][0]
    assert "three sections" in request.text      # the calling function's docstring
    assert request.history_text == ""            # no history by default

A reply may be a string or any JSON-serializable value (for calls with a
``response_format``). A reply to ``role.choose`` is the option ID. An
exception reply is raised and stays queued, so it also answers the model
loop's retries.
"""
from __future__ import annotations

import contextlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

_UNSET = object()


@dataclass
class Request:
    """One request a role sent, as the model would receive it."""

    system: str
    history: list[dict]
    current: list[dict]
    blocks: list[dict] = field(repr=False, default_factory=list)

    @property
    def text(self) -> str:
        """Instructions, the docstring, context blocks and the prompt."""
        return "\n\n".join(part for part in [self.system, *(b.get("text", "") for b in self.current)] if part)

    @property
    def prompt(self) -> str:
        texts = [b.get("text", "") for b in self.current if b.get("text")]
        return texts[-1] if texts else ""

    @property
    def history_text(self) -> str:
        return "\n\n".join(b.get("text", "") for b in self.history if b.get("text"))


class RoleCalls(dict):
    """``calls[role]`` is the list of requests that role sent."""

    def __missing__(self, role):
        return []


def _split(content: list[dict]) -> Request:
    system = "\n\n".join(b.get("text", "") for b in content if b.get("role") == "system")
    rest = [b for b in content if b.get("role") != "system"]
    index = len(rest)
    while index > 0 and rest[index - 1].get("role") == "user":
        index -= 1
    return Request(system=system, history=rest[:index], current=rest[index:], blocks=list(content))


def _is_choice(request: Request) -> bool:
    return any('Call: {"call":' in (b.get("text") or "") for b in request.current)


def _reply_text(reply: Any, request: Request) -> str:
    if isinstance(reply, BaseException):
        raise reply
    if isinstance(reply, str):
        if _is_choice(request) and not reply.lstrip().startswith("{"):
            return json.dumps({"call": reply})
        return reply
    return json.dumps(reply, ensure_ascii=False, default=str)


@contextmanager
def script_roles(script: Mapping[Any, Sequence[Any]]) -> Iterator[RoleCalls]:
    """Answer each role's calls from ``script`` and record the requests."""
    from openprogram import Runtime

    calls = RoleCalls()
    saved = []
    runtimes = []
    try:
        for role, replies in script.items():
            pending = list(replies)
            label = getattr(role, "_role_name", "") or type(role).__name__

            def answer(content, model=None, *, _role=role, _pending=pending, _label=label, **_):
                request = _split(content)
                calls.setdefault(_role, []).append(request)
                if not _pending:
                    raise AssertionError(f"No scripted reply left for role {_label!r}.")
                if isinstance(_pending[0], BaseException):
                    raise _pending[0]
                return _reply_text(_pending.pop(0), request)

            runtime = Runtime(call=answer)
            runtimes.append(runtime)
            saved.append((role, role.__dict__.get("runtime", _UNSET)))
            role.runtime = runtime
        yield calls
    finally:
        for role, previous in saved:
            if previous is _UNSET:
                role.__dict__.pop("runtime", None)
            else:
                role.runtime = previous
        for runtime in runtimes:
            with contextlib.suppress(Exception):
                runtime.close()


__all__ = ["Request", "RoleCalls", "script_roles"]
