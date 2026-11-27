"""The scheduler and remind tools — self-register via @function on import.

The Scheduler itself (its task store and the worker that runs due tasks)
is part of the runtime: ``openprogram.runtime.scheduler``.
"""

from . import remind  # noqa: F401 -- registers the remind tool via function(...) on import
from .cron import CRON_ALIAS_NAME, DESCRIPTION, NAME, SPEC, execute

__all__ = ["NAME", "CRON_ALIAS_NAME", "SPEC", "execute", "DESCRIPTION"]
