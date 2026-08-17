"""Compatibility alias for :mod:`openprogram_server.server`.

Server application assembly is owned by ``apps/server``. This module preserves
the established import path without creating a second set of server globals.
"""

from importlib import import_module
import sys

_server = import_module("openprogram_server.server")


sys.modules[__name__] = _server
