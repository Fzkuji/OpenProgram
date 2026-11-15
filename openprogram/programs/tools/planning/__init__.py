"""Plan-mode, task-list and Goal functions.

``goal`` registers the ``create_goal`` / ``get_goal`` / ``update_goal``
tools on import and lists the ``goal`` Program in ``PROGRAM_ENTRIES``,
which the registry registers through the context ``tool:goal``.
"""

from . import goal, open_work, plan_mode, todo

__all__ = ["goal", "open_work", "plan_mode", "todo"]
