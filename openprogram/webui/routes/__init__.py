"""FastAPI route registrations split out from server.py by topic.

Each module exposes ``register(app)`` that attaches its handlers to a
shared FastAPI ``app``. server.create_app() calls them in order. This
keeps server.py focused on app construction, state, and the WS handler.

Modules import from ``openprogram.webui.server`` *lazily inside handler
bodies* — at the time ``register(app)`` runs, server.py is still mid-
import (create_app is mid-execution), so top-level ``from .server import
X`` would see a partial module. Inside handlers (which run later) it's
fine.
"""
