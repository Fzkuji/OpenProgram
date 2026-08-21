"""All model-aware Programs and complete Workflows."""

import os as _os

from openprogram.programs._registry import load_agentic_modules as _load_modules

_load_modules(_os.path.dirname(__file__))

del _os, _load_modules
