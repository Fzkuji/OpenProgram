"""Owner-authenticated Goal projection and mutation endpoints."""
from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app):
    from openprogram.programs.workflow.goal import GoalStateUnavailable

    @app.exception_handler(GoalStateUnavailable)
    async def goal_state_unavailable(_request, _error):
        return JSONResponse(content={"error": "GoalStateUnavailable"}, status_code=503)

    @app.get("/api/sessions/{session_id}/goal")
    async def get_goal(session_id: str):
        import openprogram.programs.workflow.goal as goal_module
        goal = goal_module.load_goal(session_id)
        if not goal:
            return JSONResponse(content={"error": "GoalNotFound"}, status_code=404)
        return JSONResponse(content={"goal": goal})

    @app.post("/api/sessions/{session_id}/goal")
    async def mutate_goal(session_id: str, body: dict = None):
        import openprogram.programs.workflow.goal as goal_module
        payload = body or {}
        action = str(payload.get("action") or "").strip()
        if action == "resume":
            goal = goal_module.load_goal(session_id)
            if not goal or goal.get("status") not in goal_module.RESUMABLE_STATUSES:
                return JSONResponse(content={"error": "GoalNotResumable"}, status_code=409)
            try:
                goal_module.check_goal_preconditions(goal, payload.get("expected"))
            except ValueError as exc:
                return JSONResponse(content={"error": str(exc)}, status_code=409)
            return JSONResponse(content={
                "goal": goal,
                "invoke": goal_module._resume_invocation(goal),
            })
        try:
            goal = goal_module.apply_goal_action(
                session_id,
                action,
                **{key: value for key, value in payload.items() if key != "action"},
            )
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=409)
        response = {"goal": goal}
        if (
            action == "answer"
            and goal.get("status") == "paused"
            and goal.get("phase") == "answer_received"
        ):
            response["invoke"] = goal_module._resume_invocation(goal)
        return JSONResponse(content=response)
