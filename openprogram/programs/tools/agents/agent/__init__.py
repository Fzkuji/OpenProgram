"""agent tool family — same-session agent spawning.

``agent`` spawns another agent in the same session; ``list_jobs`` /
``job_output`` inspect its background
(run_in_background=true) form. One tool per subdirectory; self-register
via @function on import.
"""
from .agent import agent
from .list_jobs import list_jobs
from .job_output import job_output

__all__ = ["agent", "list_jobs", "job_output"]
