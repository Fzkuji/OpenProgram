"""The failures of a call that have a code, as the model reads them.

A coded answer starts with its code and a colon, so a model can tell a wrong
name from a machine that is off, and can tell the user what to grant. Each
says what its row of the design page says it does. A failure with no code
(an application that refused a click) keeps its own sentence after ``Error:``.
"""
from __future__ import annotations


class CallError(RuntimeError):
    """A failure with a code. ``sent`` says whether any input may have reached the target."""

    code = ""
    sent = False

    def text(self) -> str:
        return f"{self.code}: {self}"


class UnknownTarget(CallError):
    code = "UNKNOWN_TARGET"


class UnknownAction(CallError):
    code = "UNKNOWN_ACTION"


class NotSupported(CallError):
    code = "NOT_SUPPORTED"


class BadArgument(CallError):
    code = "BAD_ARGUMENT"


class PermissionMissing(CallError):
    code = "PERMISSION_MISSING"


class DependencyMissing(CallError):
    code = "DEPENDENCY_MISSING"


class MachineUnreachable(CallError):
    """The machine did not answer and nothing was sent."""
    code = "MACHINE_UNREACHABLE"


class OutcomeUnknown(CallError):
    """A step was sent and no answer came: it may or may not have taken effect."""
    code = "OUTCOME_UNKNOWN"
    sent = True


class ScreenBusy(CallError):
    code = "SCREEN_BUSY"


class ElementGone(CallError):
    code = "ELEMENT_GONE"


class Timeout(CallError):
    """The deadline passed. A provider that stops after steps took effect names them and sets ``sent``."""
    code = "TIMEOUT"


CODES = tuple(cls.code for cls in (UnknownTarget, UnknownAction, NotSupported, BadArgument, PermissionMissing,
                                   DependencyMissing, MachineUnreachable, OutcomeUnknown, ScreenBusy, ElementGone,
                                   Timeout))

__all__ = ["BadArgument", "CODES", "CallError", "DependencyMissing", "ElementGone", "MachineUnreachable",
           "NotSupported", "OutcomeUnknown", "PermissionMissing", "ScreenBusy", "Timeout", "UnknownAction",
           "UnknownTarget"]
