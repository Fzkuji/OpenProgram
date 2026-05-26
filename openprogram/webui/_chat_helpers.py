"""Chat-input parsing and MessageStore → WebSocket bridge.

Extracted from server.py to keep the main module focused on app
construction and state. Both helpers are referenced via lazy
``from openprogram.webui import server as _s`` so they can reach the
broadcast / discovery / store helpers without import cycles.
"""
from __future__ import annotations

import json


def wire_message_store_broadcast() -> None:
    """Install a one-shot global listener on the process-wide store.

    Idempotent: the first call registers, subsequent ones are no-ops. The
    listener lives for the process lifetime; there's no matching unsubscribe
    because the store itself is the single source of truth.
    """
    from openprogram.webui import server as _s
    if getattr(wire_message_store_broadcast, "_installed", False):
        return
    store = _s._get_message_store()

    def _on_frame(session_id: str, frame: dict) -> None:
        envelope = {"type": "chat_response", "data": dict(frame)}
        envelope["data"]["session_id"] = session_id
        _s._broadcast(json.dumps(envelope, default=str))

    store.subscribe_all(_on_frame)
    wire_message_store_broadcast._installed = True  # type: ignore[attr-defined]


def parse_chat_input(text: str) -> dict:
    """Parse user input to determine intent.

    Returns dict with keys:
      - action: "run" / "query" / "spawn" / "merge"
      - function: function name (if applicable)
      - kwargs: dict of arguments (if applicable)
      - raw: original text

    Slash commands handled here:

      /spawn <label>: <prompt text>
          User-initiated sub-agent spawn. Materialises a peer session,
          runs one turn against ``prompt``, attaches a pointer node
          back into this session (caller-hung off the user's own
          message — no intermediate assistant turn needed). The label
          (1-3 words) becomes the sub-session title + sidebar handle.

      /merge <sid_a> <sid_b> [...]: <message>
          Consolidate N peer sub-sessions into a single reply on this
          (target) session. Writes a multi-parent ContextCommit whose
          parent_ids cover the target's prior commit + each peer's
          latest commit id. ``message`` is the merge instruction the
          merge agent receives alongside each peer's final text.
    """
    from openprogram.webui import server as _s
    text = text.strip()
    lower = text.lower()

    # /task [--clean | --inherit] [--async | --sync] [label]: prompt
    #   --clean    → new root in this session (no parent context)
    #   --inherit  → fork off this turn (default)
    #   --async    → submit to TaskRunner, return immediately with task_id
    #   --sync     → block until done (default, kept for explicit selection)
    # Legacy /spawn kept as alias.
    matched_prefix = None
    for p in ("/task", "/spawn"):
        if lower.startswith(p):
            matched_prefix = p
            break
    if matched_prefix is not None:
        rest = text[len(matched_prefix):].strip()
        # Strip optional --clean / --inherit / --async / --sync flags
        # in any order before the label.
        context = "inherit"
        wait = True
        for _ in range(4):  # at most 4 flags
            lower_rest = rest.lower()
            consumed = False
            for flag, val in (
                ("--clean", ("context", "clean")),
                ("--inherit", ("context", "inherit")),
                ("--async", ("wait", False)),
                ("--sync", ("wait", True)),
            ):
                # Match if the flag is followed by whitespace, end of
                # string, or a ':' (so ``/task --async: prompt`` and
                # ``/task --clean alpha: prompt`` both parse).
                tail = lower_rest[len(flag):len(flag) + 1]
                if lower_rest.startswith(flag) and (
                    not tail or tail.isspace() or tail == ":"
                ):
                    rest = rest[len(flag):].lstrip()
                    if val[0] == "context":
                        context = val[1]
                    else:
                        wait = val[1]
                    consumed = True
                    break
            if not consumed:
                break
        # Split label from prompt on the first `:`. Empty label is allowed.
        label = ""
        prompt = rest
        if ":" in rest:
            label, prompt = rest.split(":", 1)
            label = label.strip()
            prompt = prompt.strip()
        return {
            "action": "spawn",
            "label": label,
            "prompt": prompt,
            "context": context,
            "wait": wait,
            "raw": text,
        }

    # /merge sid_a sid_b [...]: message
    if lower.startswith("/merge"):
        rest = text[len("/merge"):].strip()
        # Split ids from message on first `:`. Ids are whitespace-
        # separated tokens; empty message is allowed.
        message = ""
        ids_blob = rest
        if ":" in rest:
            ids_blob, message = rest.split(":", 1)
            message = message.strip()
        sub_sessions = [s for s in ids_blob.split() if s]
        return {
            "action": "merge",
            "sub_sessions": sub_sessions,
            "message": message,
            "raw": text,
        }

    # All ``action="run"`` parse branches (create / edit / run / bare
    # function name) were removed when @agentic_function dispatch
    # unified onto ``dispatcher.dispatch_forced_tool_call``. UI-level
    # function invocation now goes through ``POST /api/function/{name}``
    # — chat text never short-circuits into a direct function call.
    # ``/run xxx`` typed into chat falls through to the LLM as plain
    # text, so the user can ask the assistant to invoke the function
    # via its tool slot instead.

    # Default: general LLM query
    return {"action": "query", "raw": text}
