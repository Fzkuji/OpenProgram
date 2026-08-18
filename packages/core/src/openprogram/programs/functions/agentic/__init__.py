"""@agentic_function bodies — composable LLM-aware functions.

Modules under this package register their ``@agentic_function`` entries
via the decorator's side effect when imported. The list of modules to
import is driven by :data:`openprogram.programs._registry.AGENTIC_MODULES`
(NOT by walking the directory) — explicit beats implicit.

Complete harness applications live under the sibling ``applications/`` tier.
"""
import os as _os

from ..._registry import load_agentic_modules as _load_agentic_modules

_load_agentic_modules(_os.path.dirname(__file__))

del _os, _load_agentic_modules
