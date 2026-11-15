"""Current-conversation managed process history, output and stop controls."""
from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from openprogram.host.processes import ProcessStore, control
from openprogram.host.processes.store import public_record
from .lifecycle import _actor_and_session


def _browser_pages():
    """The browser's page-resource module, or ``None`` while the browser is not enabled."""
    from openprogram.programs._host import services as _services
    browser = _services.lookup("browser")
    return browser.pages if browser is not None else None


def _authorize(request, session_id, record=None, *, stop=False):
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.authorization import authorize_session_action, ExecutionAuthorizationError
    from openprogram.runtime.execution.conversation_scope import authorize_conversation_execution
    from openprogram.runtime.execution.public import project_id_for_session

    actor, bound_session = _actor_and_session(request)
    action = "execution.cancel" if stop else "execution.snapshot"
    if bound_session is not None and bound_session != session_id:
        raise ExecutionAuthorizationError("not_found")
    # A program can outlive the execution that started it; the conversation
    # that owns the program still decides below.
    store = default_store()
    execution = store.get_execution(record["execution_id"]) if record and record.get("execution_id") else None
    if execution is not None:
        if execution.session_id != record["session_id"]:
            raise ExecutionAuthorizationError("not_found")
        return authorize_conversation_execution(actor or {}, action, execution,
            store=store, session_id=session_id, bound_session=bound_session)
    if record and record["session_id"] != session_id:
        raise ExecutionAuthorizationError("not_found")
    return authorize_session_action(actor or {}, action, {
        "session_id": session_id, "project_id": project_id_for_session(session_id),
    })


def _error(exc):
    from openprogram.runtime.execution.authorization import ExecutionAuthorizationError
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
            from openprogram.runtime.execution import default_store
            from openprogram.runtime.execution.conversation_scope import conversation_executions
            from openprogram.host.resources.session import ResourceUseStore
            scope = {item.execution_id: item for item in conversation_executions(default_store(), session_id)}
            from openprogram.runtime.execution.authorization import ExecutionAuthorizationError
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
            pages = _browser_pages()
            browser_items, current_branch_id, current_branch_name = (
                pages.project_conversation_resources(session_id) if pages is not None else ([], None, None)
            )
            items.extend(browser_items)
            from openprogram.programs._host.ui.bindings import list_bindings
            items.extend(list_bindings(session_id))
            return JSONResponse(
                {
                    "items": items, "now": time.time(),
                    "current_branch_id": current_branch_id,
                    "current_branch_name": current_branch_name,
                },
                headers={"Cache-Control": "no-store"},
            )
        except Exception as exc:
            return _error(exc)

    @app.get("/api/session/{session_id}/native-window/frame")
    def native_window_frame(session_id: str, request: Request, window_id: int):
        """The latest screenshot the Agent took of the app window it operates."""
        try:
            _authorize(request, session_id)
            from fastapi.responses import Response
            from openprogram.host.resources.native_window import latest_window
            entry = latest_window(session_id)
            if not entry or entry["window_id"] != window_id or entry.get("frame") is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return Response(entry["frame"], media_type="image/png",
                            headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)

    @app.get("/api/session/{session_id}/native-window")
    def native_window(session_id: str, request: Request):
        """Which app window the Agent last operated, for a reconnecting App."""
        try:
            _authorize(request, session_id)
            from openprogram.host.resources.native_window import latest_window, public_window
            entry = latest_window(session_id)
            return JSONResponse({"window": public_window(entry) if entry else None},
                                headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return _error(exc)

    @app.post("/api/session/{session_id}/resources/attach-web")
    def attach_web_resource(session_id: str, request: Request, body: dict):
        try:
            _authorize(request, session_id, stop=True)
            window_id, tab_id = body.get("window_id"), body.get("tab_id")
            if any(not isinstance(value, str) or not value or len(value) > 512
                   for value in (window_id, tab_id)):
                return JSONResponse({"error": "invalid_command"}, status_code=400)
            from openprogram.interfaces.server.ws_actions.webtab import attach_existing_page
            items = attach_existing_page(session_id, window_id, tab_id)
            return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})
        except ValueError:
            return JSONResponse({"error": "stale_page"}, status_code=409)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/session/{session_id}/resources/{resource_id}/control")
    async def session_resource_control(session_id: str, resource_id: str, request: Request):
        try:
            _authorize(request, session_id)
            actor, _bound = _actor_and_session(request)
            body = await request.json()
            if not isinstance(body, dict):
                return JSONResponse({"error": "invalid_command"}, status_code=400)
            action = body.get("action")
            command_id = body.get("command_id")
            generation = body.get("generation")
            if action not in {"pause", "resume"} or not isinstance(command_id, str) or not command_id:
                return JSONResponse({"error": "invalid_command"}, status_code=400)
            if type(generation) is not int:
                return JSONResponse({"error": "invalid_command"}, status_code=400)
            pages = _browser_pages()
            if pages is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            row = await pages.apply_resource_control(
                conversation_session_id=session_id, resource_id=resource_id,
                action=action, command_id=command_id, generation=generation,
                actor=actor,
            )
            payload = dict(row)
            payload["now"] = time.time()
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        except KeyError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except ValueError as exc:
            code = str(exc) if str(exc) in {"stale_generation", "unsupported_action"} else "invalid_command"
            return JSONResponse({"error": code}, status_code=409 if code == "stale_generation" else 400)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc) or "conflict"}, status_code=409)
        except Exception as exc:
            return _error(exc)

    @app.post("/api/session/{session_id}/resources/{resource_id}/close")
    def session_resource_close(session_id: str, resource_id: str, request: Request):
        try:
            _authorize(request, session_id)
            pages = _browser_pages()
            if pages is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            from openprogram.interfaces.server.ws_actions.webtab import close_page_resource
            associations = pages.BrowserResourceStore().associations_for_page(resource_id)
            if not any(
                session_id in {item.get("conversation_session_id"), item.get("session_id")}
                for item in associations
            ):
                return JSONResponse({"error": "not_found"}, status_code=404)
            rows = close_page_resource(resource_id)
            return JSONResponse({"items": rows}, headers={"Cache-Control": "no-store"})
        except KeyError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except Exception as exc:
            return _error(exc)

    @app.get("/api/session/{session_id}/processes")
    def session_processes(session_id: str, request: Request):
        try:
            _authorize(request, session_id)
            from openprogram.runtime.execution import default_store
            from openprogram.runtime.execution.authorization import ExecutionAuthorizationError
            from openprogram.runtime.execution.conversation_scope import conversation_executions
            scope = {item.execution_id: item for item in conversation_executions(default_store(), session_id)}
            store = ProcessStore()
            records = store.list(session_id, scope)
            items = []
            for record in records:
                execution = scope.get(record.get("execution_id"))
                if execution is not None:
                    if execution.session_id != record["session_id"]:
                        raise ExecutionAuthorizationError("not_found")
                elif record["session_id"] != session_id:
                    raise ExecutionAuthorizationError("not_found")
                # A bash command still in its first wait shows as an active
                # tool call; it becomes a program once it moves to the background.
                if record.get("background", True):
                    items.append(public_record(record))
            from openprogram.runtime.context.session_db import default_db
            from openprogram.runtime.execution.activity_calls import active_tool_calls, agent_calls
            calls = active_tool_calls(default_store(), scope.values())
            spawns = agent_calls(default_store(), session_id, list(scope.values()), default_db().get_messages)
            return JSONResponse({"items": items, "calls": calls, "spawns": spawns, "now": time.time()},
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
