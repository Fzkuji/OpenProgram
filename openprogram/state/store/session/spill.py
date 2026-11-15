"""Large-node spilling — a WRITE-path concern.

A node whose text blows past the render cap gets its full text written
once to the session's ``large_nodes/`` dir at the moment it is recorded,
and the path stamped on ``metadata.spilled``. Rendering then only reads
that stamp and cites the file.

Why the write path: spilling used to happen inside the renderer, which
made "build the prompt" a side-effecting operation. Two renders of the
same history wrote files twice, a replay of an old turn could mint new
files, and a read-only consumer (the Context tab, a token count) mutated
the session directory just by looking. Recording is the one moment a
node's text is genuinely new, so it is the one place the spill belongs.

The spill file is plain multi-line text, not the node's JSON record:
JSON is one minified line, so ``read``'s line-based offset/limit can't
index into it. A real .txt lets the marker cite an exact line range and
the ``read()`` call that fetches the elided middle.
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Any, Callable, Optional

# How big a node that is not a tool result may be before it is spilled
# when recorded (a summary or a program's return value; a tool result is
# bounded where it is made, ``bound_text``, and a person's or a model's
# message is never cut). Env-overridable, and read only when a node is
# made: rendering reads the cap a node was recorded under
# (``elide_spilled``), so changing this never changes an existing
# conversation.
NODE_RENDER_CAP = int(os.environ.get("OPENPROGRAM_NODE_RENDER_CAP", "32000"))

# The context window, in tokens, of the model the running tool's result goes
# to, and that model's own ``tool_output_weight``; the agent loop sets both
# around its tool calls. 0 means unknown.
_window_tokens: ContextVar[int] = ContextVar("tool_result_window_tokens", default=0)
_output_weight: ContextVar[int] = ContextVar("tool_result_output_weight", default=0)

# How much one tool result may weigh when the model declares nothing
# (``Model.tool_output_weight``), as Codex's default ``truncation_policy``
# (10,000 tokens). Weight is ``state.store.session.weight``: a CJK character counts 1,
# anything else 0.25, so this is about 40,000 characters of English and
# 10,000 of Chinese.
DEFAULT_TOOL_OUTPUT_WEIGHT = 10_000

# One tool result may take at most this share of the model's window.
WINDOW_SHARE = 0.3

# Share of the kept text ``bound_text`` takes from the front; the rest is
# the tail.
HEAD_SHARE = 0.6

# Weight ``bound_text`` sets aside for the note between head and tail
# before it measures the note itself.
_NOTE_ROOM = 150


def result_cap(tool_cap: Any = None) -> int:
    """How much a stored tool result may weigh (``state.store.session.weight``): the
    model's ``tool_output_weight`` (``DEFAULT_TOOL_OUTPUT_WEIGHT`` when it
    declares none), at most ``WINDOW_SHARE`` of its window, and at most
    ``tool_cap`` when the tool declared a smaller one."""
    cap = _output_weight.get() or DEFAULT_TOOL_OUTPUT_WEIGHT
    window = _window_tokens.get()
    if window > 0:
        cap = min(cap, int(window * WINDOW_SHARE))
    if isinstance(tool_cap, int) and not isinstance(tool_cap, bool) and tool_cap > 0:
        cap = min(cap, tool_cap)
    return max(1, cap)


def set_window(tokens: int | None, output_weight: int | None = None) -> Token:
    """Say the running tool calls' results go to a model with a window of
    ``tokens`` that takes results of up to ``output_weight``; reset the
    returned token with ``reset_window`` when the calls are done."""
    return (_window_tokens.set(max(0, int(tokens or 0))),
            _output_weight.set(max(0, int(output_weight or 0))))


def reset_window(token: Any) -> None:
    window, weight = token
    _output_weight.reset(weight)
    _window_tokens.reset(window)


# Spilling on/off. Off ⇒ nothing is written to large_nodes/ and an
# over-cap node falls back to plain char truncation with a generic
# pointer — i.e. the model loses the "read the elided middle by line
# number" affordance. That contrast is the point of the ablation.
SPILL_ENABLED = os.environ.get(
    "OPENPROGRAM_NODE_SPILL", "on",
).strip().lower() not in {"0", "off", "false", "no", ""}

HEAD_LINES = 60   # lines kept from the top of an over-cap node
TAIL_LINES = 40   # lines kept from the bottom


def large_dir_for(session_directory: Optional[str]) -> Optional[str]:
    """The ``large_nodes/`` directory inside a session's directory."""
    if not session_directory:
        return None
    return os.path.join(session_directory, "large_nodes")


def spill_if_large(text: Any, *, node_key: str,
                   large_dir: Optional[str]) -> Optional[dict]:
    """Write ``text`` to ``large_dir`` when it is over cap.

    Returns the ``spilled`` stamp for ``node.metadata`` — ``{"path",
    "total_lines", "total_chars", "cap"}`` — or None when the node is under
    cap, is too few lines to usefully elide, or no spill dir is known. The
    stamp records the cap it was written under, so every later render of
    the node cuts it at the same place whatever the cap is then.
    """
    # Module-level lookup so a monkeypatched flag takes effect.
    import openprogram.state.store.session.spill as _self
    if not _self.SPILL_ENABLED:
        return None
    cap = _self.NODE_RENDER_CAP
    if not isinstance(text, str) or len(text) <= cap:
        return None
    if not large_dir or not node_key:
        return None
    lines = text.splitlines()
    if len(lines) <= (HEAD_LINES + TAIL_LINES):
        return None
    try:
        os.makedirs(large_dir, exist_ok=True)
        path = os.path.join(large_dir, f"{node_key}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        return None
    return {"path": path, "total_lines": len(lines), "total_chars": len(text), "cap": cap}


def bounded_when_made(node: Any) -> bool:
    """Whether ``node`` is a tool result a model called for, which the
    agent loop bounded when it was made (``bound_text``, through
    ``tool_dispatch._as_stored``). Its text is what the turn sent, so it
    is neither spilled when recorded nor cut when rendered: a later
    request sends it as the turn did.
    """
    md = getattr(node, "metadata", None) or {}
    is_code = getattr(node, "is_code", None)
    return bool(callable(is_code) and is_code() and md.get("tool_call_id")
                and not md.get("imported_from") and not md.get("provider_executed"))


def never_cut(node: Any) -> bool:
    """Whether ``node`` is a message a person or a model wrote (a user
    message, a reply, a sub-agent's report, a summary). Its text is sent
    whole in its turn and on every later request: it is neither spilled
    when recorded nor cut when rendered, as Codex, Claude Code, OpenCode
    and Pi treat input. Only a stamp written before this rule (an old node)
    still cuts it, at the cap the stamp recorded."""
    is_user = getattr(node, "is_user", None)
    is_llm = getattr(node, "is_llm", None)
    return bool((callable(is_user) and is_user()) or (callable(is_llm) and is_llm()))


# The cap a render applies to a node recorded without one: a stamp written
# before stamps carried their cap, or a node with no stamp at all. A fixed
# number, not ``NODE_RENDER_CAP``: rendering reads only the stored node, so
# changing the cap (or its environment variable) never changes the bytes
# of a conversation that already exists.
RECORDED_CAP = 32_000


def elide_spilled(text: str, spilled: Optional[dict]) -> str:
    """Render-side: head + pointer + tail for a node over its recorded cap.

    A pure function of the node: the cap is the one its stamp recorded
    (``RECORDED_CAP`` for a node without one), never the running cap. A
    node within it passes through unchanged.
    """
    cap = RECORDED_CAP
    if isinstance(spilled, dict) and isinstance(spilled.get("cap"), int) and spilled["cap"] > 0:
        cap = spilled["cap"]
    if len(text) <= cap:
        return text
    if not isinstance(spilled, dict):
        # No spill file (spilling off, or the write failed): the node is
        # still over cap and must not go to the model whole.
        return _char_elide(text, cap)
    path = spilled.get("path")
    lines = text.splitlines()
    if not path or len(lines) <= (HEAD_LINES + TAIL_LINES):
        return _char_elide(text, cap)
    total = len(lines)
    head = "\n".join(lines[:HEAD_LINES])
    tail = "\n".join(lines[-TAIL_LINES:])
    mid_start = HEAD_LINES + 1        # 1-based first elided line
    mid_end = total - TAIL_LINES      # 1-based last elided line
    n_mid = mid_end - mid_start + 1
    return (
        f"{head}\n\n"
        f"[... lines {mid_start}-{mid_end} ({n_mid:,} lines) of this "
        f"node elided. Full text saved to {path} ({total:,} lines). "
        f"To read the elided middle: "
        f'read("{path}", offset={mid_start}, limit={n_mid}). ...]\n\n'
        f"{tail}"
    )


def bound_text(text: str, save: Callable[[str], Any], cap: int | None = None) -> str:
    """Bound a text to ``cap`` (``result_cap()`` when None) once, when it
    is made. The cap is a weight (``state.store.session.weight``), so a Chinese text
    and an English one are cut at about the same number of tokens.

    A text the model reads in its turn and again on every later request
    (a tool result) is cut here, not at render: rendering an over-cap node
    would send different bytes from the turn's. ``save(text)`` keeps the
    whole text and returns its path. The result keeps the head
    (``HEAD_SHARE`` of what is kept) and the tail, cut on line breaks
    where there are any, with the path and the line range to read. The
    note is measured before the tail is chosen, so the result never
    weighs more than the cap and the line range it names is exactly the
    lines left out.
    """
    from openprogram.state.store.session.weight import quarters

    cap = result_cap() if cap is None else max(1, int(cap))
    cap_q = cap * 4
    total_q = quarters(text)
    if total_q <= cap_q:
        return text
    try:
        path = save(text)
    except Exception:  # noqa: BLE001 - the bound must hold without a copy
        path = None
    total_lines = text.count("\n") + 1
    kept_q = max(cap_q - _NOTE_ROOM * 4, cap_q // 2)
    head_end = _forward(text, 0, int(kept_q * HEAD_SHARE))
    tail_start = _backward(text, kept_q - quarters(text[:head_end]))
    # Snap to a line break when one is near, so the line range is exact.
    newline = text.rfind("\n", head_end // 2, head_end)
    if newline >= 0:
        head_end = newline + 1
    # A note heavier than its room takes it from the tail. The note's
    # numbers depend on where the tail starts, so settle the two together:
    # each pass only moves the tail start later, and the note changes by a
    # digit at most, so it settles in a few passes.
    note = ""
    for _ in range(16):
        tail_start = _snap_tail(text, max(head_end, tail_start))
        note = _bound_note(text, head_end, tail_start, total_lines, path)
        over = quarters(text[:head_end]) + quarters(note) + quarters(text[tail_start:]) - cap_q
        if over <= 0:
            return text[:head_end] + note + text[tail_start:]
        tail_q = quarters(text[tail_start:])
        if over <= tail_q:
            tail_start = _forward(text, tail_start, over, at_least=True)
        else:
            # The note leaves no room for a tail (a very small cap or a
            # very long path): keep less of the head instead.
            tail_start = len(text)
            head_end = _forward(text, 0, max(0, quarters(text[:head_end]) - (over - tail_q)))
    if quarters(note) > cap_q:
        # A cap smaller than any note that names the file.
        note = f"[... {len(text):,} chars elided ...]"
        return note[:_forward(note, 0, cap_q)]
    return text[:_forward(text, 0, cap_q - quarters(note))] + note


def _forward(text: str, start: int, budget_q: int, *, at_least: bool = False) -> int:
    """The index past ``start`` where the text in between reaches
    ``budget_q`` quarters: the last index within it, or with ``at_least``
    the first index at or past it."""
    from openprogram.state.store.session.weight import quarters

    spent = 0
    index = start
    while index < len(text):
        step = quarters(text[index])
        if spent + step > budget_q:
            if at_least and spent < budget_q:
                return index + 1
            return index
        spent += step
        index += 1
    return index


def _backward(text: str, budget_q: int) -> int:
    """The start of the longest tail of ``text`` within ``budget_q`` quarters."""
    from openprogram.state.store.session.weight import quarters

    spent = 0
    index = len(text)
    while index > 0:
        step = quarters(text[index - 1])
        if spent + step > budget_q:
            break
        spent += step
        index -= 1
    return index


def _snap_tail(text: str, tail_start: int) -> int:
    """``tail_start`` moved to just after the next line break, when one is
    within the first half of the tail."""
    if tail_start >= len(text):
        return len(text)
    newline = text.find("\n", tail_start, tail_start + (len(text) - tail_start) // 2)
    return newline + 1 if newline >= 0 else tail_start


def _bound_note(text: str, head_end: int, tail_start: int,
                total_lines: int, path: Any) -> str:
    """The note ``bound_text`` puts between the head and the tail. The
    path in the ``read`` call is a JSON string literal, so a Windows path's
    backslashes reach the model escaped as the call needs them."""
    import json as _json
    first = text.count("\n", 0, head_end) + 1      # 1-based line the cut starts in
    last = text.count("\n", 0, tail_start)         # 1-based line before the tail
    elided = tail_start - head_end
    if path is None:
        where = "The whole text was not saved."
    elif last >= first:
        where = (f"Full text saved to {path} ({total_lines:,} lines). "
                 f"To read the elided part: read({_json.dumps(str(path), ensure_ascii=False)}, "
                 f"offset={first}, limit={last - first + 1}).")
    else:
        where = f"Full text saved to {path} ({total_lines:,} lines)."
    return f"\n[... {elided:,} of {len(text):,} chars elided. {where} ...]\n\n"


def _char_elide(text: str, cap: int) -> str:
    """Fallback when there's no usable spill file: char-level head/tail."""
    head_c = int(cap * 0.6)
    tail_c = cap - head_c
    elided = len(text) - head_c - tail_c
    return (
        text[:head_c]
        + f"\n\n[... {elided:,} chars elided of {len(text):,} total in this "
        f"node; full artifacts are saved as files in the working directory "
        f"— read those for complete content. ...]\n\n"
        + text[-tail_c:]
    )


__all__ = [
    "NODE_RENDER_CAP",
    "RECORDED_CAP",
    "SPILL_ENABLED",
    "WINDOW_SHARE",
    "large_dir_for",
    "spill_if_large",
    "bounded_when_made",
    "never_cut",
    "elide_spilled",
    "bound_text",
    "result_cap",
    "DEFAULT_TOOL_OUTPUT_WEIGHT",
    "set_window",
    "reset_window",
]
