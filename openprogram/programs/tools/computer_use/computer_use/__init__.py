"""``computer_use``: one tool that operates screens, application windows and browsers on any machine.

- ``tool.py`` — the tool: its description, routing, guides on first use, what permission code asks
- ``contract.py`` — what every target is operated through (``Provider``)
- ``registry.py`` — which targets exist now: the machines registry and what each connection kind yields
- ``vocabulary.py`` — one set of actions and arguments, the families, the carried-over names
- ``providers/`` — one module per connection kind; the only place a target is named
- ``locks.py`` — who may act on a machine, an application or a tab at a time, across processes
- ``errors.py`` — the coded failures a model reads
- ``code.py`` — Python in the isolated GUI interpreter, with ``cu`` bound to the call's target
- ``desktop/`` — the platform backends that read and operate application windows and the display
- ``sandbox/`` — the isolated interpreter ``code`` runs in, and the broker it calls through
"""
from .tool import DESCRIPTION, NAME, computer_use

__all__ = ["DESCRIPTION", "NAME", "computer_use"]
