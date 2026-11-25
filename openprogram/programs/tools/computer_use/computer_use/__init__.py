"""``computer_use``: one tool that operates screens, application windows and browsers on any machine.

Dependencies point one way: the tool, through routing, to the providers, to the platform backends.

- ``tool.py`` — the tool: its fixed description and parameters, a call checked and run, what permission asks
- ``devices.py`` — ``<devices>``: what can be operated now, beside the conversation's messages
- ``type_guides.py`` — each type's guide and each target's note, delivered with the first call that needs them
- ``context.py`` — the conversation, agent and environment a call is served for
- ``contract.py`` — what every target is operated through (``Provider``); ``errors.py`` — the coded failures
- ``routing/`` — which targets exist (``registry``), one path for steps (``dispatch``), the locks (``locks``)
- ``actions/`` — the one vocabulary of actions and arguments, and key names in one spelling
- ``code_mode/`` — Python with ``cu`` bound to the call's target, in the isolated interpreter (``sandbox/``)
- ``providers/`` — the targets: ``window/``, ``screen/`` (this computer's, a virtual machine's),
  ``browser/`` (OpenProgram's own, the user's), ``paired.py`` (another computer's OpenProgram)
- ``desktop/`` — the platform backends: ``macos/``, ``windows/``, ``linux/``, what they share in
  ``common/``, and ``embedding/`` (another application's window shown in an App tab)
- ``guides/`` — the guide text of each type and of code
"""
from .tool import DESCRIPTION, NAME, computer_use

__all__ = ["DESCRIPTION", "NAME", "computer_use"]
