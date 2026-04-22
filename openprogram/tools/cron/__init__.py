"""cron tool + worker."""

from .cron import DESCRIPTION, NAME, SPEC, execute
from .worker import list_next, match, run_forever, run_once

TOOL = {
    "spec": SPEC,
    "execute": execute,
    "max_result_size_chars": 40_000,
}

__all__ = [
    "NAME", "SPEC", "TOOL", "execute", "DESCRIPTION",
    "match", "run_forever", "run_once", "list_next",
]
