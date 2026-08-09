"""agent tool family — same-session agent spawning.

``agent`` spawns another agent in the same session; ``task_list`` /
``task_output`` / ``task_stop`` manage its background
(run_in_background=true) form. One tool per subdirectory; self-register
via @function on import.
"""
from .agent import agent
from .task_list import task_list
from .task_output import task_output
from .task_stop import task_stop

__all__ = ["agent", "task_list", "task_output", "task_stop"]
