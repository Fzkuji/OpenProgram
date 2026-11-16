"""Tasks and their steps: task_start / task_step / task_list self-register
on import. A task is one list in the todo store and a step is one todo."""

from .task_list import task_list
from .task_start import task_start
from .task_step import task_step

__all__ = ["task_list", "task_start", "task_step"]
