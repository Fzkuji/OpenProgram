"""Tasks and their steps: task_start / task_step / task_list self-register
on import. A task is one list in the todo store and a step is one todo.

Importing the package also registers how the board follows a
conversation's replies (``runtime_hooks``)."""

from .task_list import task_list
from .task_start import task_start
from .task_step import task_step
from . import runtime_hooks as _runtime_hooks

_runtime_hooks.register()

__all__ = ["task_list", "task_start", "task_step"]
