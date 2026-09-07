"""Current-conversation managed process history, output and stop controls."""
from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from openprogram.processes import ProcessStore, control
from openprogram.processes.store import public_record
from .lifecycle import _actor_and_session


def _authorize(request, session_id, record=None, *, stop=False):
    from openprogram.execution import default_store
    from openprogram.execution.authorization import authorize_session_action, ExecutionAuthorizationError
    from openprogram.execution.conversation_scope import authorize_conversation_execution
    from openprogram.execution.public import project_id_for_session

    actor, bound_session = _actor_and_session(request)
    action = "execution.cancel" if stop else "execution.snapshot"
    if bound_session is not None and bound_session != session_id:
        raise ExecutionAuthorizationError("not_found")
    if record and record.get("execution_id"):
        store = default_store()
        execution = store.get_execution(record["execution_id"])
        if execution is None or execution.session_id != record["session_id"]:
            raise ExecutionAuthorizationError("not_found")
        return authorize_conversation_execution(actor or {}, action, execution,
            store=store, session_id=session_id, bound_session=bound_session)
    if record and record["session_id"] != session_id:
        raise ExecutionAuthorizationError("not_found")
    return authorize_session_action(actor or {}, action, {
        "session_id": session_id, "project_id": project_id_for_session(session_id),
    })


def _error(exc):
    from openprogram.execution.authorization import ExecutionAuthorizationError
    if isinstance(exc, (ExecutionAuthorizationError, KeyError)):
        return JSONResponse({"error": "not_found"}, status_code=404)
    # Storage/control failures are explicit; do not hide missing history behind
    # a successful empty list, nor disclose paths or command input in errors.
    return JSONResponse({"error": "process_unavailable", "reason": type(exc).__name__}, status_code=503)


def register(app):
    @app.get("/api/session/{session_id}/resources")
    def session_resources(session_id: str, request: Request):
        try:
            _authorize(request, session_id)
            from openprogram.execution import default_store
            from openprogram.execution.conversation_scope import conversation_executions
            from openprogram.session_resources import ResourceUseStore
            scope = {item.execution_id: item for item in conversation_executions(default_store(), session_id)}
            from openprogram.execution.authorization import ExecutionAuthorizationError
            def check_owner(record):
                execution_id = record.get("execution_id")
                if execution_id:
                    execution = scope.get(execution_id)
                    if execution is None or execution.session_id != record["session_id"]:
                        raise ExecutionAuthorizationError("not_found")
                elif record["session_id"] != session_id:
                    raise ExecutionAuthorizationError("not_found")
            items = []
            for record in ResourceUseStore().list(session_id, scope):
                check_owner(record)
                items.append({**record, "source": "usage", "status": "attached" if record["kind"] in {"vm", "desktop"} else "in_use"})
            from openprogram.processes.store import ACTIVE
            for record in ProcessStore().list(session_id, scope):
                if record["status"] not in ACTIVE:
                    continue
                check_owner(record)
                public = public_record(record)
                items.append({
                    "id": record["id"], "session_id": record["session_id"],
                    "execution_id": record.get("execution_id"), "source": "process",
                    "kind": record["backend_id"] if record["backend_id"] in {"docker", "ssh"} else "process",
                    "title": public.get("display", {}).get("name") or "Process",
                    "target": record.get("cwd") or record["backend_id"],
                    "status": record["status"], "started_at": record["started_at"],
                })
            return JSONResponse({"items": items, "now": time.time()}, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)

    @app.get("/api/session/{session_id}/processes")
    def session_processes(session_id: str, request: Request):
        try:
            _authorize(request, session_id)
            from openprogram.execution import default_store
            from openprogram.execution.authorization import ExecutionAuthorizationError
            from openprogram.execution.conversation_scope import conversation_executions
            scope = {item.execution_id: item for item in conversation_executions(default_store(), session_id)}
            store = ProcessStore()
            records = store.list(session_id, scope)
            items = []
            for record in records:
                if record.get("execution_id"):
                    execution = scope.get(record["execution_id"])
                    if execution is None or execution.session_id != record["session_id"]:
                        raise ExecutionAuthorizationError("not_found")
                elif record["session_id"] != session_id:
                    raise ExecutionAuthorizationError("not_found")
                items.append(public_record(record))
            return JSONResponse({"items": items, "now": time.time()},
                                headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)

    def selected(process_id, request, *, stop=False):
        store = ProcessStore()
        record = store.get(process_id)
        if record is None:
            raise KeyError(process_id)
        _, bound_session = _actor_and_session(request)
        session_id = request.query_params.get("session_id") or bound_session or record["session_id"]
        _authorize(request, session_id, record, stop=stop)
        return store, record

    @app.get("/api/process/{process_id}")
    def process_detail(process_id: str, request: Request):
        try:
            store, record = selected(process_id, request)
            output = store.output(process_id)
            record = store.get(process_id)
            return JSONResponse({"process": public_record(record), "output": output},
                                headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)

    @app.post("/api/process/{process_id}/stop")
    def stop_process(process_id: str, request: Request):
        try:
            store, _ = selected(process_id, request, stop=True)
            record = control(store, process_id, "stop")
            return JSONResponse({"process": public_record(record)},
                                headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)
