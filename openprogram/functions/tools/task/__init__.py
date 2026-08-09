"""task tool family — same-session agent spawning.

``task`` spawns another agent in the same session; ``await_task`` /
``cancel_task`` manage its async (wait=False) form. One tool per
subdirectory; self-register via @function on import.
"""
from .task import task
from .await_task import await_task
from .cancel_task import cancel_task

__all__ = ["task", "await_task", "cancel_task"]
