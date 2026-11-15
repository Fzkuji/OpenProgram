"""The instruction of a ``--print`` run, from its argument and standard input.

* ``--print -``, or ``--print`` with no value while standard input is not a
  terminal: the whole of standard input is the instruction.
* ``--print TEXT`` with standard input piped: what is already in the pipe,
  or arrives within :data:`PIPED_INPUT_GRACE_S`, is read whole and appended
  as a block the model can tell apart from the instruction::

      TEXT

      <stdin>
      ...
      </stdin>

  A pipe that stays empty for that long is not read, so a process started
  with an open but unused standard input (a harness, a supervisor) does not
  hang.
* ``--print TEXT`` on a terminal: the text alone.

An empty instruction is a usage error (exit 2).

On Windows a pipe cannot be polled with ``select``; a reader thread takes
the first byte instead, and the rest is read once it came.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import IO

#: How long ``--print TEXT`` waits for piped input to start.
PIPED_INPUT_GRACE_S = 0.2

#: ``--print`` given with no value.
FROM_STDIN = "-"


class PromptInputError(ValueError):
    """The instruction could not be formed; the command exits 2."""


def _is_terminal(stream: IO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _read_all(stream: IO) -> str:
    data = stream.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return data or ""


def _first_within(stream: IO, grace: float) -> str | None:
    """Whatever the pipe holds when data arrives within ``grace``, read to
    its end; ``None`` when nothing came in time."""
    try:
        fileno = stream.fileno()
    except (AttributeError, ValueError, OSError):
        fileno = None
    if fileno is not None and os.name != "nt":
        import select

        try:
            ready, _, _ = select.select([fileno], [], [], grace)
        except (OSError, ValueError):
            ready = [fileno]
        return _read_all(stream) if ready else None
    # Windows pipes, and streams with no descriptor (a test's StringIO): a
    # thread takes the first character, the rest is read after it.
    box: dict[str, str] = {}
    done = threading.Event()

    def _take() -> None:
        try:
            box["first"] = stream.read(1) or ""
        except Exception:  # noqa: BLE001 - nothing readable is no input
            box["first"] = ""
        finally:
            done.set()

    threading.Thread(target=_take, daemon=True, name="openprogram-print-stdin").start()
    if not done.wait(grace):
        return None
    first = box.get("first") or ""
    if isinstance(first, bytes):
        first = first.decode("utf-8", errors="replace")
    if not first:
        return ""
    return first + _read_all(stream)


def with_piped_input(text: str, piped: str) -> str:
    """``text`` followed by ``piped`` as a ``<stdin>`` block."""
    body = piped if piped.endswith("\n") else piped + "\n"
    return f"{text}\n\n<stdin>\n{body}</stdin>"


def resolve_prompt(arg: str | None, stdin: IO | None = None, *,
                   schema_from_stdin: bool = False,
                   grace: float | None = None) -> str:
    """The instruction for ``--print ARG``, refused (:class:`PromptInputError`)
    when it is longer than one message may be
    (``turn_input.MAX_USER_INPUT_CHARS``)."""
    from openprogram.runtime.conversation.turn_input import input_too_large

    text = _resolve_prompt(arg, stdin, schema_from_stdin=schema_from_stdin, grace=grace)
    too_large = input_too_large(text)
    if too_large:
        raise PromptInputError(too_large)
    return text


def _resolve_prompt(arg: str | None, stdin: IO | None = None, *,
                    schema_from_stdin: bool = False,
                    grace: float | None = None) -> str:
    """The instruction for ``--print ARG`` (``"-"`` when given no value).

    ``schema_from_stdin`` is ``--json-schema -``, which takes standard input
    for itself, so the instruction may not come from there. Raises
    :class:`PromptInputError` for an empty instruction or that conflict.
    """
    stream = sys.stdin if stdin is None else stdin
    wait = PIPED_INPUT_GRACE_S if grace is None else grace
    if arg is None or arg == FROM_STDIN:
        if schema_from_stdin:
            raise PromptInputError("prompt and JSON schema cannot both read from stdin")
        if stream is None or _is_terminal(stream):
            raise PromptInputError("--print needs a prompt: give it as text, or pipe it in")
        text = _read_all(stream)
        if not text.strip():
            raise PromptInputError("the prompt read from stdin is empty")
        return text
    if not arg.strip():
        raise PromptInputError("--print needs a prompt")
    if schema_from_stdin or stream is None or _is_terminal(stream):
        return arg
    piped = _first_within(stream, wait)
    if not piped or not piped.strip():
        return arg
    return with_piped_input(arg, piped)


__all__ = ["FROM_STDIN", "PIPED_INPUT_GRACE_S", "PromptInputError", "resolve_prompt",
           "with_piped_input"]
