"""The providers, all behind ``contract.Provider``.

Importing this package lets each module offer what it opens, by connection
method (``registry.offer``): this computer's windows and screen
(``window/``, ``screen/local.py``) and browsers (``browser/``) for ``local``,
a virtual machine's screen for ``osworld`` (``screen/osworld.py``, which also
registers that method with the device layer), another computer's
OpenProgram for ``openprogram`` (``paired.py``). A module that cannot load on this platform leaves its targets
out; the others stay.

Nothing outside this package names a target. To add one, add its module
here and to ``_MODULES``.
"""
from __future__ import annotations

import importlib
import logging

_log = logging.getLogger(__name__)

# In the order the description lists this computer's targets.
_MODULES = ("window.provider", "browser.built_in", "screen.osworld", "paired")

for _name in _MODULES:
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{_name}":
            _log.warning("the %s providers did not load", _name, exc_info=True)
    except Exception:
        _log.warning("the %s providers did not load", _name, exc_info=True)
