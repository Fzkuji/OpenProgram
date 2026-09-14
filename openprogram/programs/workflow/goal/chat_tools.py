"""Short state operations used by the ordinary working chat Agent."""
from openprogram.programs._runtime import function


def _session():
    from openprogram.agent.run_control import get_current_session_id
    sid = get_current_session_id()
    if not sid:
        raise ValueError("Goal tools require an active conversation")
    return sid


@function(name="create_goal", toolset=["core"], unsafe_in=["wechat", "telegram"],
          description="Create a persistent objective for this chat only when explicitly requested. Continue working in this chat and plan with the todo tools. Do not infer a Goal from ordinary requests. Specify token_budget only when requested. Fails if an unfinished goal exists.")
def create_goal(objective: str, token_budget: int | None = None) -> dict:
    from . import chat
    return chat.create(_session(), objective, token_budget)


@function(name="get_goal", toolset=["core"],
          description="Read the current chat Goal, its status, cumulative usage and associated todo plan.")
def get_goal() -> dict:
    import openprogram.programs.workflow.goal as goals
    from . import chat
    sid = _session()
    goal = goals.load_goal(sid)
    return {"goal": goal, "todos": chat.todos(sid, goal) if goal else []}


@function(name="update_goal", toolset=["core"], unsafe_in=["wechat", "telegram"],
          description="Mark the current Goal complete only after verifying every requirement against actual evidence and finishing its todo plan. Mark blocked only after the SAME blocker recurs across at least three consecutive Goal turns with no independent work available. Never shrink the objective. Budget exhaustion is not completion. User pause/resume and budget changes use the Goal controls.")
def update_goal(status: str) -> dict:
    from . import chat
    return chat.update(_session(), status, expected=chat.current_identity())
