"""All model-aware Programs and complete Workflows."""

import os as _os
import importlib as _importlib
import sys as _sys

# Published historical snapshots retain their original shared-module imports.
for _name in ("io", "output", "sources", "wechat", "wechat_visual"):
    _module = _importlib.import_module(f"{__name__}._reports.{_name}")
    globals()[f"report_{_name}"] = _module
    _sys.modules[f"{__name__}.report_{_name}"] = _module
del _name, _module, _importlib, _sys

from openprogram.programs._registry import load_agentic_modules as _load_modules

_load_modules(_os.path.dirname(__file__))

del _os, _load_modules
