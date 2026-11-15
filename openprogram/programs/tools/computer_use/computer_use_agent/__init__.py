"""``computer_use_agent``: one tool that operates application windows, built-in browser Pages and a VM.

- ``tool.py`` — the tool: catalogue description, routing, guides on first use, approval
- ``targets/`` — ``app``, ``browser``, ``vm``: each target's actions, guide and approval
- ``code.py`` — Python in the isolated GUI interpreter, with ``cu.<target>`` objects
- ``desktop/`` — the platform backends that read and operate application windows
- ``sandbox/`` — the isolated interpreter ``code`` runs in, and the broker it calls through
"""
from .tool import DESCRIPTION, NAME, TARGETS, computer_use_agent, evaluates, guide, requires_approval

__all__ = ["DESCRIPTION", "NAME", "TARGETS", "computer_use_agent", "evaluates", "guide", "requires_approval"]
