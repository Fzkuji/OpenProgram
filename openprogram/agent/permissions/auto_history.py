"""Idempotent Auto verdicts and session counters in canonical execution events."""
from __future__ import annotations

import hashlib
import json
import os
import time

from openprogram.worktree.context import current_worktree_path

_EVENT = "permission.auto.reviewed"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()


class AutoHistory:
    def __init__(self, store, execution_id, session_id, epoch, action_id):
        self.store = store
        self.execution_id = execution_id
        self.session_id = session_id
        self.epoch = epoch
        self.action_id = action_id

    def _existing(self, connection, action_id):
        row = connection.execute(
            "SELECT payload_json FROM execution_events WHERE execution_id = ? AND kind = ? "
            "AND json_extract(payload_json, '$.action_id') = ? ORDER BY sequence DESC LIMIT 1",
            (self.execution_id, _EVENT, action_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get(self):
        from contextlib import closing
        with closing(self.store._connect()) as connection:
            return self._existing(connection, self.action_id)

    def record(self, *, blocked, reason, code, approved=False):
        action_id = self.action_id + (":approved" if approved else "")
        with self.store._transaction() as connection:
            existing = self._existing(connection, action_id)
            if existing is not None:
                return existing
            execution = self.store._require_execution(connection, self.execution_id)
            if execution.session_id != self.session_id:
                raise ValueError("auto review session mismatch")
            row = connection.execute(
                "SELECT payload_json FROM execution_events WHERE kind = ? "
                "AND json_extract(payload_json, '$.session_id') = ? "
                "AND json_extract(payload_json, '$.epoch') = ? ORDER BY sequence DESC LIMIT 1",
                (_EVENT, self.session_id, self.epoch),
            ).fetchone()
            previous = json.loads(row[0]) if row else {}
            total = int(previous.get("total", 0)) + int(blocked)
            if approved and int(previous.get("total", 0)) >= 20:
                total = 0
            consecutive = int(previous.get("consecutive", 0)) + 1 if blocked else 0
            value = dict(action_id=action_id, session_id=self.session_id, epoch=self.epoch,
                         blocked=blocked, reason=reason, code=code, total=total,
                         consecutive=consecutive, fallback=blocked and (consecutive >= 3 or total >= 20))
            self.store._append_event(connection, execution_id=self.execution_id,
                execution_version=execution.status_version, kind=_EVENT, payload=value, created_at=time.time())
            return value


def for_operation(request, name, call_id, args, operation_id=None, *, files=None):
    from openprogram.agent.run_control import get_current_execution_id
    execution_id = get_current_execution_id()
    if not execution_id:
        return None
    from openprogram.execution import default_store
    store = default_store()
    execution = store.get_execution(execution_id)
    if execution is None or execution.session_id != request.session_id:
        raise ValueError("auto review execution mismatch")
    epoch = _digest([request.principal_id, getattr(request, "_permission_version", 0), request.permission_mode])
    action_id = _digest([execution_id, operation_id, call_id, name, args, epoch,
                         repr(request.permission_rules), request.user_text,
                         current_worktree_path() or os.getcwd(), request.additional_working_dirs, files])
    return AutoHistory(store, execution_id, request.session_id, epoch, action_id)
