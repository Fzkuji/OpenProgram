"""What an unattended turn is told about the run it is in.

A turn no one is watching (``openprogram --print`` in process, or a chat
command the Worker runs with ``interaction="non-interactive"``) carries :data:`RUN_NOTE` beside its
instruction (``TurnRequest.run_note``), not stored with it: the request
carries it once, in front of the instruction, with the same bytes every
round. Without it a model treats the run as a chat and ends its turn asking
whether to go on, which ends the run; in a benchmark trial that left a task
half done at a third of its time.
"""
from __future__ import annotations

RUN_NOTE = (
    "This run was started from the command line and no one is watching it. No one "
    "can answer a question or approve anything before it ends, and your first reply "
    "without a tool call ends the run. Carry the task through to the end yourself: "
    "do not stop to ask whether to continue or to offer next steps. Check your work "
    "with the tools before you give your final reply."
)

__all__ = ["RUN_NOTE"]
