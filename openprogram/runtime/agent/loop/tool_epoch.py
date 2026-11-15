"""The tools array is fixed within an epoch.

An epoch is the run of requests that share one cached prefix: it starts with
a conversation, with the first request after a compaction summary, and with
a model switch. Inside it the tools array only grows (a searched tool, a new
resident tool); nothing is taken out and nothing an earlier request sent is
rewritten, because a model that checks the tool set (Anthropic's newest
models) rejects a request whose array lost or changed an entry, and every
provider re-reads everything after a changed entry.

So what the user changes mid-epoch is enforced where the call is dispatched,
not in the array:

- plan mode never takes a tool out (``permissions.lifecycle``): the calls are
  refused by ``permission_decision``, as Codex refuses ``update_plan`` in its
  Plan mode;
- a whole-tool deny rule and a capability the identity lacks take a tool out
  from the next epoch only (``withheld``); until then its calls are refused;
- the structured-output submit tool, once an epoch's request sent it, is
  sent by every later request of the epoch; a request that needs it when the
  epoch has none starts a new epoch.

The epoch's record is kept per conversation and epoch key in memory and on
the conversation's tool-snapshot node, so a Worker restart reads it back.

Design: docs/reference/design/function/deferred-tools.html
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

#: The records of recent epochs, by ``(session, key)``.
_records: dict[tuple[str, str], "EpochRecord"] = {}
_lock = threading.Lock()
_KEPT = 512


@dataclass(frozen=True)
class EpochRecord:
    key: str
    #: Tools left out of the array for the whole epoch.
    withheld: frozenset[str]
    #: The submit tool's ``{"name", "description", "parameters"}``, once sent.
    submit: Optional[dict[str, Any]] = None

    def as_json(self) -> dict[str, Any]:
        return {"key": self.key, "withheld": sorted(self.withheld), "submit": self.submit}


def _first_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def epoch_key(model: Any, messages: Any) -> str:
    """Names the epoch a request belongs to: its model and the first message
    it sends (the conversation's first message, or the summary that
    replaced it)."""
    first = next(iter(messages or ()), None)
    raw = json.dumps([
        str(getattr(model, "provider", "") or ""), str(getattr(model, "id", "") or ""),
        str(getattr(first, "role", "") or ""), _first_text(first),
    ], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _stored(session_id: Optional[str], key: str) -> Optional[EpochRecord]:
    if not session_id:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.context.tool_snapshot_node import latest_recorded_tool_snapshot

        snapshot = latest_recorded_tool_snapshot(default_db(), session_id) or {}
    except Exception:
        return None
    epoch = snapshot.get("epoch")
    if not isinstance(epoch, dict) or epoch.get("key") != key:
        return None
    submit = epoch.get("submit")
    return EpochRecord(key, frozenset(str(n) for n in epoch.get("withheld") or ()),
                       submit if isinstance(submit, dict) else None)


def withheld_now(tools: Any) -> frozenset[str]:
    """The tools a new epoch leaves out: every call of them is refused."""
    out = set()
    for tool in tools or ():
        check: Optional[Callable[[], bool]] = getattr(tool, "_permission_epoch_visible", None)
        if callable(check):
            try:
                if not check():
                    out.add(tool.name)
            except Exception:
                continue
    return frozenset(out)


def epoch_for(session_id: Optional[str], key: str, tools: Any,
              submit: Optional[dict[str, Any]] = None) -> EpochRecord:
    """The record of epoch ``key``, made now from ``tools`` when it is new.

    ``submit`` is the submit tool this request needs; an epoch that has none
    yet is replaced by one that has it (a new epoch).
    """
    lane = (session_id or "", key)
    with _lock:
        record = _records.get(lane)
    if record is None:
        record = _stored(session_id, key)
    if record is None or (submit is not None and record.submit is None):
        record = EpochRecord(key, withheld_now(tools), submit)
    elif submit is not None and record.submit != submit:
        # Another schema in the same epoch: the array's entry would change.
        record = EpochRecord(key, record.withheld, submit)
    with _lock:
        _records[lane] = record
        while len(_records) > _KEPT:
            _records.pop(next(iter(_records)))
    return record


def forget(session_id: Optional[str] = None) -> None:
    """Drop the kept records (of one conversation, or all)."""
    with _lock:
        for lane in [lane for lane in _records if session_id is None or lane[0] == (session_id or "")]:
            _records.pop(lane, None)


__all__ = ["EpochRecord", "epoch_for", "epoch_key", "forget", "withheld_now"]
