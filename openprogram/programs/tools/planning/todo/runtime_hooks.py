"""How the task board follows a conversation's replies.

The board (``shared``) holds a conversation's tasks and their steps; a
reply's blocks belong to the task it works on. The conversation runtime sits
below Programs and never imports the board: it names the moments of a reply
(``openprogram.runtime.conversation.turn_hooks``) and this module registers
the board's part in each when the task tools load:

* ``reply_task_owner`` — the task a reply continues from its first block:
  the open task of the Goal the turn works for (``shared.turn_owner``);
* ``reply_segment`` — the reply's segment of that task, begun at its first
  real call (``shared.open_turn_segment``);
* ``reply_ended`` — a reply that ended normally closes or carries the task
  (``shared.close_at_turn_end``);
* ``unfinished_work`` — an open task's unfinished steps are work a
  scheduled always-on check-in looks at.

Each handler reads ``shared`` when it is called, so a test that replaces
one of its functions is what the runtime reaches.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.conversation import turn_hooks

OWNER = "tasks"


def _reply_task_owner(request: Any) -> dict | None:
    from openprogram.programs.tools.planning.goal import chat as goal_chat
    from openprogram.programs.tools.planning.todo import shared as tasks

    goal = goal_chat.current_identity() or getattr(request, "goal_context", None)
    return tasks.turn_owner(request.session_id, tasks.scope_of(request), goal, getattr(request, "branch_id", None))


def _reply_segment(request: Any, assistant_msg_id: str, task_id: str) -> None:
    from openprogram.programs.tools.planning.todo import shared as tasks

    tasks.open_turn_segment(request.session_id, assistant_msg_id, task_id,
                            scope=tasks.scope_of(request, assistant_msg_id),
                            branch_id=getattr(request, "branch_id", None))


def _reply_ended(request: Any, assistant_msg_id: str) -> None:
    from openprogram.programs.tools.planning.todo import shared as tasks

    tasks.close_at_turn_end(request.session_id, assistant_msg_id,
                            scope=tasks.scope_of(request, assistant_msg_id),
                            branch_id=getattr(request, "branch_id", None))


def _unfinished_work(session_id: str) -> bool:
    """Unfinished steps of an open task of the conversation's own agent.

    Steps of a closed task are nobody's to finish: a task that never
    advanced ends with its steps as they were, and a sub-agent's run that
    stopped leaves its own behind.
    """
    from openprogram.programs.tools.planning.todo import shared as tasks

    open_tasks = {str(task.get("id")) for task in tasks.load_tasks(session_id)
                  if task.get("status") == "open" and not task.get("scope")}
    return any(str(item.get("plan") or "") in open_tasks and item.get("status") in tasks.UNFINISHED
               for item in tasks.load(session_id))


def register() -> None:
    turn_hooks.register("reply_task_owner", OWNER, _reply_task_owner)
    turn_hooks.register("reply_segment", OWNER, _reply_segment)
    turn_hooks.register("reply_ended", OWNER, _reply_ended)
    turn_hooks.register("unfinished_work", OWNER, _unfinished_work)
