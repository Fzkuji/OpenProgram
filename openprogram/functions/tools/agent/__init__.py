"""agent tool family — same-session agent spawning.

``agent`` spawns another agent in the same session; ``list_tasks`` /
``task_output`` / ``task_stop`` manage its background
(run_in_background=true) form. One tool per subdirectory; self-register
via @function on import.
"""
from .agent import agent
from .list_tasks import list_tasks
from .task_output import task_output
from .task_stop import task_stop

__all__ = ["agent", "list_tasks", "task_output", "task_stop"]
