"""What every part of the tool reads about the call it serves: the tool's name, the conversation, the agent that operates, the machine its shell runs on, and whether code can run."""
from __future__ import annotations

from .code_mode import calls as code_mode


NAME = "computer_use"


def _session_id() -> str:
    try:
        from openprogram.runtime.execution.run_control import get_current_session_id
        return get_current_session_id() or "default"
    except Exception:
        return "default"


def operator(session: str) -> str:
    """The key of what this agent last read of the screen: the conversation, plus the agent when it is a job.

    The conversation's own turns share one key, so a turn acts on the window
    the turn before it read. An agent started in the conversation
    (the agent tool) runs beside them as a job. Each
    keeps its own window states, element ids and code interpreter, so one
    agent reading or running code never replaces what another is acting on.
    """
    try:
        from openprogram.runtime.jobs.runner import _current_job_id
        job = _current_job_id.get()
    except Exception:
        job = None
    return f"{session}\n{job}" if job else session


def _environment():
    """The machine this conversation's commands run on, or None when that cannot be told."""
    try:
        from openprogram.host import machines
        return machines.current_environment()
    except Exception:
        return None


def _code_available() -> bool:
    try:
        return bool(code_mode.available())
    except Exception:
        return False
