"""pdf tool."""

from .pdf import DESCRIPTION, NAME, SPEC, _tool_check_fn, execute

TOOL = {
    "spec": SPEC,
    "execute": execute,
    "check_fn": _tool_check_fn,
    "max_result_size_chars": 100_000,
}

__all__ = ["NAME", "SPEC", "TOOL", "execute", "DESCRIPTION"]
