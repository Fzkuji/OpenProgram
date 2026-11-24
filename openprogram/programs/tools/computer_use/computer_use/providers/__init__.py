"""The providers: one module per connection kind, all behind ``contract.Provider``.

Importing this package registers every kind with the machines registry
(``openprogram.host.machines.kinds``). A module that cannot load on this
platform leaves its kind out; the others stay.

Nothing outside this package names a target. To add a kind, add its module
here and to ``_MODULES``.
"""
from __future__ import annotations

import importlib
import logging

_log = logging.getLogger(__name__)

# In the order the description lists this computer's targets.
_MODULES = ("desktop", "built_in_browser", "osworld")

for _name in _MODULES:
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{_name}":
            _log.warning("the %s providers did not load", _name, exc_info=True)
    except Exception:
        _log.warning("the %s providers did not load", _name, exc_info=True)
