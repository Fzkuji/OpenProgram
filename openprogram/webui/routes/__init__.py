"""Compatibility path for FastAPI route registrations.

Each module exposes ``register(app)`` that attaches its handlers to a
shared FastAPI ``app``. server.create_app() calls them in order. This
keeps server.py focused on app construction, state, and the WS handler.

Modules import from ``openprogram.webui.server`` *lazily inside handler
bodies* — at the time ``register(app)`` runs, server.py is still mid-
import (create_app is mid-execution), so top-level ``from .server import
X`` would see a partial module. Inside handlers (which run later) it's
fine.
"""

from openprogram.webui import _implementation_dir


_routes_dir = _implementation_dir / "routes"
if not _routes_dir.is_dir():
    raise ImportError(f"OpenProgram Server routes are missing: {_routes_dir}")
if str(_routes_dir) not in __path__:
    __path__.append(str(_routes_dir))
