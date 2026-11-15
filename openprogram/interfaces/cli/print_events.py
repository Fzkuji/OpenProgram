"""The public, versioned events of one ``--print`` run.

What a ``--print`` run did is told as a list of events with one schema of
their own, built here from what the run left behind: the conversation's
stored messages, the usage ledger's requests, the turn journal's tool
results and the run's execution tree. The internal stores keep their own
shapes; only this module maps them, so a reader of the events (the
transcript file, a stream output) never depends on a store's layout.

Every event is::

    {"schema_version": 1, "seq": n, "execution_id": "...",
     "parent_execution_id": "..." | None, "type": "...", "data": {...}}

``seq`` counts from 1 with no holes. ``execution_id`` is the execution the
event belongs to (the run's own, a sub-agent's, the turn a Worker restart
continued the run as); ``parent_execution_id`` is that execution's parent,
``None`` for the run's own. The types:

``run``
    Which conversation, model and run note, how the run ended, and what its
    requests carried (``print_turn.run_header``).
``user``
    An instruction an execution of the run admitted.
``request``
    One model request from the ledger, with its tokens, and, for a request
    this process saw sent, its effort, ``include``, ``store`` and tools.
``reasoning``, ``assistant_text``, ``block``
    What a request returned, in the reply's order.
``tool_call``, ``tool_result``
    A call with its full arguments, and the result as the model read it.
    The stored result is the whole text the model was sent (bounded when
    it was made, with the full output in a file it names), so it is
    ``complete``.
``reply``
    The end of an execution's reply.
``permission_denied``
    A call refused by the permission decision, after its ``tool_result``:
    the tool, the call id, the refusal's code and the rule that decided, if
    one did. A ``--print`` run has no one to approve a call that would ask,
    so such a call is refused at once (``NON_INTERACTIVE``).
``gap``
    Something the run did that its stores do not hold, said where it is
    missing instead of left out: ``what`` is ``tool_result`` for a call
    with no stored result (the run stopped during it), ``requests`` when
    the ledger could not be read, ``messages`` when the run's messages
    could not be found.

A stream (``--output-format stream-json``, :class:`Stream`) adds ``init``
before everything and ``result`` after it, and, with partial messages,
``text_delta`` and ``reasoning_delta`` while the turn runs; it leaves the
``run`` event out, since ``init`` and ``result`` carry it.

The transcript file is these events, one record per line, each written as
``{"type": ..., **data}`` (:func:`transcript_record`).
"""
from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

#: The version of this schema; a change a reader must know of raises it.
SCHEMA_VERSION = 1


class _Events:
    """The events of a run as they are appended, numbered in order."""

    def __init__(self, parents: dict[str, str | None]) -> None:
        self.events: list[dict[str, Any]] = []
        self._parents = parents

    def add(self, kind: str, execution_id: str | None, data: dict[str, Any]) -> None:
        self.events.append({
            "schema_version": SCHEMA_VERSION,
            "seq": len(self.events) + 1,
            "execution_id": execution_id,
            "parent_execution_id": self._parents.get(execution_id) if execution_id else None,
            "type": kind,
            "data": data,
        })


def run_events(run: Any, *, store: Any = None) -> list[dict[str, Any]]:
    """The run's events, in the order things happened.

    A turn's assistant reply stores its blocks in order but without times;
    a request is placed before the first block that came after it, using
    the time of the next stored tool result as the bound. A call with no
    stored result (the run stopped during it) has no time of its own and
    takes the bound of the next call that has one.
    """
    from openprogram.interfaces.cli import print_turn

    if store is None:
        from openprogram.state.store.current import current_store
        store = current_store()
    session_id = run.session_id
    scope = _run_scope(run)
    executions, anchors, parents = scope["executions"], scope["anchors"], scope["parents"]
    stored = store.get_messages(session_id)
    messages = [m for m in stored if m.get("id") in anchors]
    tool_rows = {_tool_call_id(m): m for m in stored if m.get("role") == "tool"}
    journaled = _journaled_results(store, session_id, executions)
    denials = _journaled_denials(store, session_id, executions)
    requests = _request_rows(run, executions)
    events = _Events(parents)

    events.add("run", run.execution_id, {
        "session_id": session_id, "execution_id": run.execution_id,
        "model": run.model, "started_at": _iso(run.started_at), "ended": run.ended,
        "error": run.error, **print_turn.run_outcome(run), "run_note": print_turn.RUN_NOTE,
        **print_turn.run_header(run),
    })
    if scope["unread"]:
        events.add("gap", run.execution_id, {"what": "messages"})
    if requests is None:
        events.add("gap", run.execution_id, {"what": "requests"})
        requests = []
    pending = list(requests)
    number = {id(row): index for index, row in enumerate(requests, 1)}
    sent = dict(zip(map(id, requests), print_turn._pair_sent(requests, run.sent)))

    def within(execution_id: Any, owner: str | None) -> bool:
        # ``execution_id`` is ``owner`` or one of its descendants.
        seen: set = set()
        while execution_id and execution_id not in seen:
            if execution_id == owner:
                return True
            seen.add(execution_id)
            execution_id = parents.get(execution_id)
        return False

    def flush(before: float, owner: str | None = None) -> None:
        # The requests made before ``before``, in order; with an ``owner``
        # that has a parent (a sub-agent whose reply is being written) only
        # its own and its descendants', so a sub-agent's reply does not take
        # the requests its caller made after it.
        for row in list(pending):
            if row["ts"] >= before:
                break
            if (owner and parents.get(owner) and row.get("execution_id")
                    and not within(row.get("execution_id"), owner)):
                continue
            pending.remove(row)
            fact = sent.get(id(row))
            events.add("request", row.get("execution_id"), {
                "n": number[id(row)],
                "time": _iso(row["ts"]), "call_kind": row["call_kind"],
                "model": f"{row['provider']}/{row['model_id']}",
                "input_tokens": row["input_tokens"],
                "cached_input_tokens": row["cache_read_tokens"],
                "cache_write_tokens": row["cache_write_tokens"],
                "output_tokens": row["output_tokens"],
                **({"effort": fact["effort"], "include": fact["include"],
                    "store": fact["store"], "tools_count": len(fact["tools"]),
                    "tools_sha256": fact["tools_sha256"]} if fact else {}),
            })

    for message in messages:
        role = message.get("role")
        execution_id = anchors.get(message.get("id"))
        if role == "user":
            flush(float(message.get("timestamp") or 0))
            events.add("user", execution_id, {
                "time": _iso(message.get("timestamp")),
                "id": message.get("id"), "text": message.get("content") or ""})
        elif role == "assistant":
            blocks = _json_field(message.get("extra")).get("blocks") or []
            for index, block in enumerate(blocks):
                kind = block.get("type")
                if kind == "tool":
                    row = tool_rows.get(block.get("tool_call_id") or "")
                    flush(float((row or {}).get("timestamp") or 0)
                          or _next_tool_time(blocks[index + 1:], tool_rows), execution_id)
                    events.add("tool_call", execution_id, {
                        "tool_call_id": block.get("tool_call_id"),
                        "name": block.get("tool"),
                        "arguments": _arguments(row or {}, block),
                    })
                    call_id = str(block.get("tool_call_id") or "")
                    whole = journaled.get(call_id)
                    if whole is None and row is not None:
                        whole = row.get("content")
                    # Without a stored result, what the reply's block shows
                    # (the live preview), if anything.
                    content = whole if whole is not None else block.get("result")
                    events.add("tool_result", execution_id, {
                        "tool_call_id": block.get("tool_call_id"),
                        "time": _iso((row or {}).get("timestamp")),
                        "is_error": bool((row or {}).get("is_error", block.get("is_error"))),
                        "content": content,
                        "complete": whole is not None,
                    })
                    denial = denials.get(call_id)
                    if denial is not None:
                        events.add("permission_denied", execution_id, {
                            "tool_call_id": block.get("tool_call_id"),
                            "tool": block.get("tool"), **denial,
                            "arguments": _arguments(row or {}, block),
                        })
                    if whole is None:
                        events.add("gap", execution_id, {
                            "what": "tool_result", "tool_call_id": block.get("tool_call_id")})
                    continue
                if kind == "thinking" and not block.get("text"):
                    continue  # reasoning with no summary: only its signature, for the model
                flush(_next_tool_time(blocks[index + 1:], tool_rows), execution_id)
                if kind in ("text", "thinking"):
                    events.add("assistant_text" if kind == "text" else "reasoning",
                               execution_id, {"text": block.get("text") or ""})
                else:
                    events.add("block", execution_id, {"block": block})
            flush(float("inf"), execution_id)
            events.add("reply", execution_id, {
                "time": _iso(message.get("completed_at") or message.get("finished_at")),
                "started": _iso(message.get("started_at") or message.get("timestamp")),
                "id": message.get("id"), "status": message.get("status"),
                "text": message.get("content") or "",
            })
    flush(float("inf"))
    return events.events


def transcript_record(event: dict[str, Any]) -> dict[str, Any]:
    """An event as the transcript file writes it: its type, then its data."""
    return {"type": event["type"], **event["data"]}


def _run_scope(run: Any) -> dict[str, Any]:
    """The run's executions (``print_turn.run_executions``), the messages
    they wrote (each execution's instruction and reply, as its admission
    named them) by message id with the execution that wrote it, each
    execution's parent, and whether that could not all be read
    (``unread``)."""
    from openprogram.interfaces.cli import print_turn

    executions = print_turn.run_executions(run)
    scope: dict[str, Any] = {"executions": executions, "anchors": {}, "parents": {},
                             "unread": False}
    if not executions:
        return scope
    from openprogram.runtime.execution import default_store

    try:
        store = default_store().fresh_view()
        for execution_id in executions:
            record = store.get_execution_input(execution_id)
            if record is not None:
                for anchor in (record.user_message_id, record.assistant_message_id):
                    if anchor:
                        scope["anchors"][anchor] = execution_id
            if execution_id != run.execution_id:
                execution = store.get_execution(execution_id)
                scope["parents"][execution_id] = getattr(execution, "parent_execution_id", None)
    except Exception:  # noqa: BLE001 - the events still have the run's requests
        _log.warning("message anchors of %s unreadable", run.execution_id, exc_info=True)
        scope["unread"] = True
    return scope


def _journaled_results(store: Any, session_id: str, executions: frozenset[str]) -> dict[str, str]:
    """The text of each tool result the run's executions journaled, by call
    id: what the model was given. Read from the session log, so a turn the
    Worker ran is covered as well as one run here."""
    from openprogram.runtime.execution.agent_journal import TOOL_RESULT

    read = getattr(store, "read_turn_records", None)
    if not callable(read) or not executions:
        return {}
    try:
        records = read(session_id, None, TOOL_RESULT)
    except Exception:  # noqa: BLE001 - the stored rows still hold the results
        _log.warning("turn journal unreadable for %s", session_id, exc_info=True)
        return {}
    results: dict[str, str] = {}
    for record in records:
        data = record.get("data") or {}
        result = data.get("result")
        call_id = str(data.get("tool_call_id") or "")
        if not call_id or not isinstance(result, dict) or data.get("execution_id") not in executions:
            continue
        # The text channel, as ``tool.after`` gives it.
        results[call_id] = "".join(
            str(part.get("text") or "") for part in result.get("content") or ()
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return results


def _journaled_denials(store: Any, session_id: str,
                       executions: frozenset[str]) -> dict[str, dict[str, Any]]:
    """The run's calls the permission decision refused, by call id: the
    refusal's ``code``, ``layer`` and ``rule`` from the result's details."""
    from openprogram.runtime.execution.agent_journal import TOOL_RESULT

    read = getattr(store, "read_turn_records", None)
    if not callable(read) or not executions:
        return {}
    try:
        records = read(session_id, None, TOOL_RESULT)
    except Exception:  # noqa: BLE001 - the results are reported without it
        return {}
    found: dict[str, dict[str, Any]] = {}
    for record in records:
        data = record.get("data") or {}
        result = data.get("result")
        details = result.get("details") if isinstance(result, dict) else None
        call_id = str(data.get("tool_call_id") or "")
        if (not call_id or data.get("execution_id") not in executions
                or not isinstance(details, dict) or not details.get("denied")):
            continue
        found[call_id] = {"code": details.get("reason_code"), "layer": details.get("layer"),
                          "rule": details.get("rule")}
    return found


def permission_denials(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The run's refused calls (``permission_denied`` events), as the end
    record lists them: tool, call id, code and rule."""
    return [{"tool": event["data"].get("tool"), "tool_call_id": event["data"].get("tool_call_id"),
             "code": event["data"].get("code"), "rule": event["data"].get("rule")}
            for event in events if event.get("type") == "permission_denied"]


def denial_line(event: dict[str, Any]) -> str:
    """The stderr line for a refused call: ``permission denied: TOOL (WHAT)``."""
    data = event.get("data") or {}
    arguments = data.get("arguments")
    what = ""
    if isinstance(arguments, dict):
        for key in ("command", "file_path", "path", "url", "action"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                what = " ".join(value.split())
                what = what[:77] + "..." if len(what) > 80 else what
                break
    return f"permission denied: {data.get('tool')}" + (f" ({what})" if what else "")


# ---------------------------------------------------------------------------
# The stream (``--output-format stream-json``)
# ---------------------------------------------------------------------------

def init_data(run: Any) -> dict[str, Any]:
    """What a stream's ``init`` says: the transport, the Worker's version,
    the model, effort and permission mode asked for, the conversation, the
    execution and the tools the first request carried (``None`` until a
    request was seen)."""
    first = next((fact for fact in getattr(run, "sent", None) or []
                  if fact.get("call_kind") in (None, "chat")), None)
    return {
        "transport": run.transport, "worker_version": run.worker_version,
        "model": run.model, "effort": run.effort, "permission_mode": run.permission_mode,
        "session_id": run.session_id, "execution_id": run.execution_id,
        "tools": first.get("tools") if first else None,
    }


class Stream:
    """One JSON event per line on ``out``: ``init`` first, ``result`` last,
    ``seq`` counting from 1 with no holes across both and the stored events
    between them."""

    def __init__(self, out: Any = None, *, partial: bool = False) -> None:
        import sys

        self.out = out if out is not None else sys.stdout
        self.partial = partial
        self.seq = 0
        self.started = False
        self.ended = False

    def _write(self, kind: str, data: dict[str, Any], execution_id: str | None = None,
               parent: str | None = None) -> None:
        self.seq += 1
        self.out.write(json.dumps({
            "schema_version": SCHEMA_VERSION, "seq": self.seq, "execution_id": execution_id,
            "parent_execution_id": parent, "type": kind, "data": data,
        }, ensure_ascii=False, default=str) + "\n")
        self.out.flush()

    def init(self, run: Any) -> None:
        """Write ``init`` once, as soon as the run knows its transport."""
        if not self.started:
            self.started = True
            self._write("init", init_data(run), run.execution_id)

    def delta(self, run: Any, kind: str, text: str) -> None:
        """A text or reasoning delta, written only with partial messages."""
        if self.partial and text and not self.ended:
            self.init(run)
            self._write(f"{kind}_delta", {"text": text}, run.execution_id)

    def finish(self, run: Any, events: list[dict[str, Any]], record: dict[str, Any]) -> None:
        """The stored events (``run`` left out), then ``result``."""
        if self.ended:
            return
        self.init(run)
        for event in events:
            if event.get("type") == "run":
                continue
            self._write(event["type"], event["data"], event.get("execution_id"),
                        event.get("parent_execution_id"))
        self.ended = True
        self._write("result", record, run.execution_id)


def _request_rows(run: Any, executions: frozenset[str]) -> list[dict[str, Any]] | None:
    """The ledger's requests of ``executions``; ``None`` when the ledger
    could not be read."""
    from openprogram.models.usage import event as usage_event
    from openprogram.models.usage.ledger import default_ledger

    kinds = [value for name, value in vars(usage_event).items()
             if name.startswith("CALL_KIND_") and isinstance(value, str)]
    try:
        return default_ledger.calls(call_kinds=kinds, execution_ids=executions)
    except Exception:  # noqa: BLE001 - the events say the requests are missing
        _log.warning("usage ledger unreadable for %s", run.session_id, exc_info=True)
        return None


def _next_tool_time(blocks: list[dict[str, Any]], tool_rows: dict[str, dict]) -> float:
    """When the next stored tool result in ``blocks`` landed; a call with
    no stored result is passed over."""
    for block in blocks:
        if block.get("type") == "tool":
            row = tool_rows.get(block.get("tool_call_id") or "") or {}
            stored = float(row.get("timestamp") or 0)
            if stored:
                return stored
    return float("inf")


def _tool_call_id(message: dict[str, Any]) -> str:
    return str(_json_field(message.get("extra")).get("tool_use", {}).get("tool_call_id") or "")


def _arguments(row: dict[str, Any], block: dict[str, Any]) -> Any:
    value = _json_field(row.get("extra")).get("tool_use", {}).get("arguments")
    if value in (None, "", {}):
        value = block.get("input")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value:
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _iso(value: Any) -> str | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


__all__ = ["SCHEMA_VERSION", "Stream", "denial_line", "init_data", "permission_denials",
           "run_events", "transcript_record"]
