"""todo tools — session-scoped task list."""

from .todo import (
    READ_NAME,
    READ_SPEC,
    WRITE_NAME,
    WRITE_SPEC,
    read_execute,
    write_execute,
)

READ_TOOL = {"spec": READ_SPEC, "execute": read_execute}
WRITE_TOOL = {"spec": WRITE_SPEC, "execute": write_execute}

__all__ = [
    "READ_NAME",
    "WRITE_NAME",
    "READ_SPEC",
    "WRITE_SPEC",
    "READ_TOOL",
    "WRITE_TOOL",
    "read_execute",
    "write_execute",
]
