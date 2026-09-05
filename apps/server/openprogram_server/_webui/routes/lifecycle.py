"""Canonical execution command and snapshot endpoints.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only emits the resulting status envelope onto the
event bus (``ws.frame`` → server's WS forwarder).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from openprogram.events import emit_ws_frame


def _execution_payload(execution, *, event_sequence: int | None = None):
    from openprogram.execution import default_store
    from openprogram.execution.public import execution_snapshot

    resource = None
    job = None
    try:
        from openprogram.agent.job import get_runner

        runner = get_runner()
        view = runner.get_job_resource_view(execution.execution_id)
        job = runner.get_job(execution.execution_id)
        # ExecutionSnapshot.resource is the resource projection itself.  The
        # surrounding JobResourceDTO is a separate public view and must not
        # be nested into the snapshot resource field.
        resource = view.resource if view is not None else None
    except Exception:
        pass
    return execution_snapshot(
        execution, store=default_store(), resource=resource, job=job,
        job_id=getattr(job, "id", None), event_sequence=event_sequence,
    ).to_dict()


def _actor_and_session(request: Request):
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor

    state = request.scope.get("state") if isinstance(request.scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    actor = trusted_runtime_actor(request.scope, surface="rest")
    if actor is None:
        auth_state = getattr(request.app.state, "owner_auth", None)
        actor = getattr(auth_state, "authority", None)
    return actor, bound_session if isinstance(bound_session, str) else None


def _authorize_read(actor, bound_session, execution, action: str, conversation_session_id: str | None = None) -> bool:
    from openprogram.execution.authorization import ExecutionAuthorizationError, authorize_execution_action
    from openprogram.execution.public import project_id_for_session

    try:
        if conversation_session_id is not None:
            from openprogram.execution import default_store
            from openprogram.execution.conversation_scope import authorize_conversation_execution

            authorize_conversation_execution(actor, action, execution, store=default_store(),
                                             session_id=conversation_session_id, bound_session=bound_session)
            return True
        if bound_session is not None and bound_session != execution.session_id:
            return False
        authorize_execution_action(
            actor or {}, action, execution,
            {"project_id": project_id_for_session(execution.session_id),
             "session_id": execution.session_id},
        )
        return True
    except ExecutionAuthorizationError:
        return False


from openprogram.execution.public import public_event as _public_event


def register(app):
    @app.get("/api/session/{session_id}/executions")
    def api_session_executions(session_id: str, request: Request):
        """List only authorized canonical executions in the selected session."""
        import time
        from openprogram.execution import default_store
        from openprogram.execution.authorization import ExecutionAuthorizationError, authorize_session_action
        from openprogram.execution.conversation_scope import conversation_execution_scope
        from openprogram.execution.public import project_id_for_session

        actor, bound_session = _actor_and_session(request)
        if bound_session is not None and bound_session != session_id:
            return JSONResponse({"error": "not_found"}, status_code=404)
        try:
            authorize_session_action(actor, "execution.snapshot", {
                "project_id": project_id_for_session(session_id), "session_id": session_id,
            })
        except ExecutionAuthorizationError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        items = []
        executions, parents = conversation_execution_scope(default_store(), session_id)
        # Membership is proved by this scoped query; principal/action grants
        # above apply to the caller conversation, not every target session.
        for execution in executions:
            snapshot = _execution_payload(execution)
            items.append({
                "kind": "execution", "id": execution.execution_id,
                "execution_id": execution.execution_id, "session_id": execution.session_id,
                "parent_execution_id": parents.get(execution.execution_id),
                "label": (snapshot.get("display") or {}).get("label") or "execution",
                "status": execution.status.value,
                "started_at": execution.created_at, "snapshot": snapshot,
                "event_cursor": {
                    "execution_id": execution.execution_id,
                    "next_sequence": int(snapshot.get("event_sequence") or 0) + 1,
                    "snapshot_status_version": execution.status_version,
                },
            })
        return JSONResponse({"items": items, "now": time.time()},
                            headers={"Cache-Control": "no-store"})

    async def _revision(request: Request, body: dict, action: str):
        """Use the same strict revision envelope as the WebSocket surface."""
        from openprogram.execution import default_store
        from openprogram.execution.revision_public import (
            RevisionPublicError,
            submit_revision_request,
        )

        actor, bound_session = _actor_and_session(request)
        try:
            state = submit_revision_request(
                default_store(), body, action, actor=actor,
                bound_session=bound_session, surface="rest",
            )
        except RevisionPublicError as exc:
            status = 404 if exc.code == "not_found" else 400 if exc.code.startswith("invalid_") else 409
            return JSONResponse({"error": exc.code}, status_code=status)
        return JSONResponse({**state, "data": state})

    async def _command(request: Request, body: dict, operation: str):
        payload = body or {}
        from openprogram.webui.ws_actions.runtime import (
            submit_execution_control,
        )

        actor, bound_session = _actor_and_session(request)
        command, execution = await submit_execution_control(
            payload,
            operation,
            actor=actor,
            bound_session=bound_session,
            surface="rest",
        )
        command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
        has_public_snapshot = hasattr(execution, "execution_id")
        update = {
            "type": "execution.command.updated",
            "command": command_data,
        }
        if has_public_snapshot:
            execution_data = _execution_payload(execution)
            cursor = {
                "execution_id": execution_data.get("execution_id"),
                "next_sequence": int(execution_data.get("event_sequence") or execution_data.get("status_version") or 0) + 1,
                "snapshot_status_version": execution_data.get("status_version"),
            }
            update.update({"execution": execution_data, "event_cursor": cursor})
        emit_ws_frame({**update, "data": update})
        if has_public_snapshot:
            from openprogram.execution.public import execution_update_frame
            emit_ws_frame(execution_update_frame(execution_data, cursor))
        from openprogram.webui import server as _s
        if has_public_snapshot and execution_data.get("status") in {"cancelling", "cancelled"}:
            _s._release_session_occupancy_for_execution(execution_data)
        code = 200
        if command_data.get("status") == "rejected":
            code = 404 if command_data.get("rejection_code") == "not_found" else 409
            if command_data.get("rejection_code") == "invalid_command":
                code = 400
        return JSONResponse(content={**update, "data": update}, status_code=code)

    for operation in ("pause", "continue", "step", "steer", "cancel", "fork", "retry"):
        async def endpoint(request: Request, body: dict = None, _operation=operation):
            return await _command(request, body or {}, _operation)

        endpoint.__name__ = f"api_execution_{operation}"
        app.post(f"/api/execution/{operation}")(endpoint)

    for operation, path in (("wait_answer", "answer"), ("wait_decline", "decline")):
        async def endpoint(request: Request, body: dict = None, _operation=operation):
            return await _command(request, body or {}, _operation)

        endpoint.__name__ = f"api_execution_{operation}"
        app.post(f"/api/execution/wait/{path}")(endpoint)

    @app.post("/api/execution/revision/draft")
    async def api_revision_draft_create(request: Request, body: dict = None):
        return await _revision(request, body or {}, "revision.draft.create")

    @app.get("/api/execution/{execution_id}/revision/draft/{draft_id}")
    async def api_revision_draft_get(execution_id: str, draft_id: str, request: Request):
        return await _revision(request, {
            "type": "revision.draft", "action": "revision.draft.get",
            "execution_id": execution_id, "draft_id": draft_id,
        }, "revision.draft.get")

    for action, path, method in (
        ("revision.draft.replace", "replace", "put"),
        ("revision.draft.discard", "discard", "post"),
        ("revision.validate", "validate", "post"),
        ("revision.approve", "approve", "post"),
        ("revision.publish", "publish", "post"),
    ):
        async def revision_endpoint(
            request: Request, draft_id: str, body: dict = None, _action=action
        ):
            payload = body or {}
            if payload.get("draft_id") != draft_id:
                return JSONResponse({"error": "invalid_command"}, status_code=400)
            return await _revision(request, payload, _action)

        revision_endpoint.__name__ = "api_" + action.replace(".", "_")
        getattr(app, method)(f"/api/execution/revision/draft/{{draft_id}}/{path}")(
            revision_endpoint
        )

    @app.get("/api/execution/{execution_id}/debugger")
    async def api_execution_debugger_state(execution_id: str, request: Request):
        """Return the canonical inspection state for one execution.

        The debugger needs the immutable checkpoint history, unresolved
        execution-owned waits, and revision draft state together with the
        ordinary execution snapshot.  This read is authorized against the
        same execution boundary as snapshot/events and never accepts client
        supplied project or session identifiers.
        """
        from openprogram.execution import default_store
        from openprogram.execution.checkpoints import ExecutionCheckpointStore
        from openprogram.execution.effects import EffectStore
        from openprogram.execution.revision_public import project_draft_state
        from openprogram.execution.revisions import RevisionControlService
        from openprogram.execution.waits import DurableWaitStore

        store = default_store()
        execution = store.get_execution(execution_id)
        actor, bound_session = _actor_and_session(request)
        if execution is None or not _authorize_read(
            actor, bound_session, execution, "execution.snapshot",
            request.query_params.get("conversation_session_id"),
        ):
            return JSONResponse(
                {"error": "not_found", "execution_id": execution_id},
                status_code=404,
            )
        checkpoints = ExecutionCheckpointStore(store).list_for_execution(execution_id)
        revision_service = RevisionControlService(store)
        drafts = [
            project_draft_state(revision_service, draft.draft_id)
            for draft in revision_service.list_drafts_for_execution(execution_id)
        ]
        unresolved_effects = []
        for effect in EffectStore(store).list_unresolved(execution_id):
            # Effect metadata may include full provider context or tool
            # arguments. Only project the registered operation and lifecycle.
            kind = effect.metadata.get("kind")
            payload = effect.metadata.get("payload")
            tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
            unresolved_effects.append({
                "effect_id": effect.effect_id, "status": effect.status.value,
                "classification": effect.classification.value,
                "kind": kind if kind in {"provider.before", "tool.before"} else None,
                "tool_name": tool_name if kind == "tool.before" and isinstance(tool_name, str) else None,
                "created_at": effect.created_at, "updated_at": effect.updated_at,
                "dispatched_at": effect.dispatched_at,
            })
        return JSONResponse({
            "type": "execution.debugger.state",
            "execution_id": execution_id,
            "checkpoints": [checkpoint.to_dict() for checkpoint in checkpoints],
            "waits": [wait.to_dict() for wait in DurableWaitStore(store).list_open(execution_id=execution_id)],
            "drafts": drafts,
            "unresolved_effects": unresolved_effects,
        })

    @app.get("/api/execution/{execution_id}")
    async def api_execution_snapshot(execution_id: str, request: Request):
        from openprogram.execution import default_store

        execution = default_store().get_execution(execution_id)
        actor, bound_session = _actor_and_session(request)
        if execution is None or not _authorize_read(actor, bound_session, execution, "execution.snapshot", request.query_params.get("conversation_session_id")):
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        snapshot = _execution_payload(execution)
        return JSONResponse({"type": "execution.snapshot", "snapshot": snapshot, "data": snapshot})

    @app.get("/api/execution/{execution_id}/events")
    async def api_execution_events(execution_id: str, request: Request, after_sequence: int = 0):
        from openprogram.execution import default_store

        execution = default_store().get_execution(execution_id)
        actor, bound_session = _actor_and_session(request)
        if execution is None or not _authorize_read(actor, bound_session, execution, "execution.events", request.query_params.get("conversation_session_id")):
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        try:
            replay = default_store().read_event_replay(
                execution_id, after_sequence=after_sequence,
            )
        except Exception:
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        snapshot = _execution_payload(replay.execution, event_sequence=replay.cursor.next_sequence - 1)
        return JSONResponse({
            "execution_id": execution_id,
            "events": [_public_event(event) for event in replay.events],
            "event_cursor": replay.cursor.to_dict(),
            "recovery": replay.recovery,
            "snapshot": snapshot,
        })

    @app.get("/api/execution/{execution_id}/audit")
    async def api_execution_audit(execution_id: str, request: Request):
        from openprogram.execution import default_store

        store = default_store()
        execution = store.get_execution(execution_id)
        actor, bound_session = _actor_and_session(request)
        if execution is None or not _authorize_read(actor, bound_session, execution, "audit.read", request.query_params.get("conversation_session_id")):
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        try:
            store.append_audit_event(
                execution_id=execution_id, actor=actor or {}, action="audit.read",
                result="allowed", surface="rest", payload={"purpose": "view"},
            )
            events = store.list_audit_events(execution_id, actor=actor or {},
                                            conversation_session_id=request.query_params.get("conversation_session_id"))
        except Exception:
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        return JSONResponse({"execution_id": execution_id, "events": [event.to_dict() for event in events]})
