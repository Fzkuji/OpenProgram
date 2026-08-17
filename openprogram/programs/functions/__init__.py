"""Shipped callable functions, grouped by execution semantics.

``vanilla`` contains deterministic ``@function`` tools. ``agentic`` contains
LLM-aware ``@agentic_function`` bodies. Importing this package loads both so
their existing decorator registrations remain the single runtime registry.
"""

from . import vanilla as _vanilla_self_register  # noqa: F401
from . import agentic as _agentic_self_register  # noqa: F401
