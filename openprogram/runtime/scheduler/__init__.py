"""User-facing scheduled tasks built on OpenProgram's signed cron executor.

Removing a task: ``service.delete_task(task_id, by=...)`` is the one way a task
leaves ``<state>/scheduler/tasks.json``. It logs the task, its title and what
asked (the Scheduler API, the cron tool, a routine that could not be saved), so
the worker log says when a task went. A save that leaves fewer tasks first
copies the list to ``tasks.json.previous``; a task removed by mistake is read
back from there.
"""

from . import migration, reminders, service

__all__ = ["migration", "reminders", "service"]
