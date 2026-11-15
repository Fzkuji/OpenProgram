"""Owner-authenticated Goal projection and mutation endpoints."""
from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app):
    from openprogram.programs.tools.planning.goal import GoalStateUnavailable

    @app.exception_handler(GoalStateUnavailable)
    async def goal_state_unavailable(_request, _error):
        return JSONResponse(content={"error": "GoalStateUnavailable"}, status_code=503)

    # Plain functions: FastAPI runs them in its thread pool, so a Goal read
    # or change under the conversation's lock never holds the event loop.
    @app.get("/api/sessions/{session_id}/goal")
    def get_goal(session_id: str):
        import openprogram.programs.tools.planning.goal as goal_module
        goal = goal_module.load_goal(session_id)
        if not goal:
            return JSONResponse(content={"error": "GoalNotFound"}, status_code=404)
        # A Goal still waiting on a turn that already ended is settled now.
        from openprogram.programs.tools.planning.goal import chat
        if not chat.recover_unsettled(session_id, goal):
            # One paused by a turn that failed goes on by itself.
            chat.continue_after_failure(session_id, goal)
        return JSONResponse(content=goal_module.goal_projection(goal, session_id))

    @app.post("/api/sessions/{session_id}/goal/queue")
    async def goal_queue(session_id: str, body: dict = None):
        """The client's send queue holds a message for this conversation's next turn."""
        from openprogram.programs.tools.planning.goal import chat
        chat.note_user_waiting(session_id, bool((body or {}).get("waiting")))
        return JSONResponse(content={"ok": True})

    @app.post("/api/sessions/{session_id}/goal")
    def mutate_goal(session_id: str, body: dict = None):
        import openprogram.programs.tools.planning.goal as goal_module
        payload = body or {}
        action = str(payload.get("action") or "").strip()
        if action == "resume":
            goal = goal_module.load_goal(session_id)
            if not goal or goal.get("status") not in goal_module.RESUMABLE_STATUSES:
                return JSONResponse(content={"error": "GoalNotResumable"}, status_code=409)
            try:
                goal_module.check_goal_preconditions(goal, payload.get("expected"))
                from openprogram.programs.tools.planning.goal import chat
                goal = chat.resume(session_id, payload.get("expected"))
                execution = chat.start_from_controls(session_id)
            except ValueError as exc:
                return JSONResponse(content={"error": str(exc)}, status_code=409)
            response = goal_module.goal_projection(goal_module.load_goal(session_id), session_id)
            response["admission"] = execution
            return JSONResponse(content=response)
        try:
            goal = goal_module.apply_goal_action(
                session_id,
                action,
                **{key: value for key, value in payload.items() if key != "action"},
            )
        except goal_module.GoalStopUnconfirmed as exc:
            return JSONResponse(content={**goal_module.goal_projection(exc.goal, session_id), "stop_error": str(exc)})
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=409)
        return JSONResponse(content=goal_module.goal_projection(goal, session_id))
