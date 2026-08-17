"""Compatibility alias for :mod:`openprogram_server.server`.

Server application assembly is owned by ``apps/server``. This module preserves
the established import path without creating a second set of server globals.
"""

from importlib import import_module, util
from pathlib import Path
import sys


try:
    _server = import_module("openprogram_server.server")
except ModuleNotFoundError as exc:
    if exc.name != "openprogram_server":
        raise
    # A source checkout can be executed without installing the root project.
    # Load the application package from its declared workspace in that case;
    # wheels and editable installs use the normal package import above.
    _package_dir = Path(__file__).resolve().parents[2] / "apps/server/openprogram_server"
    _spec = util.spec_from_file_location(
        "openprogram_server",
        _package_dir / "__init__.py",
        submodule_search_locations=[str(_package_dir)],
    )
    if _spec is None or _spec.loader is None:
        raise
    _package = util.module_from_spec(_spec)
    sys.modules["openprogram_server"] = _package
    _spec.loader.exec_module(_package)
    _server = import_module("openprogram_server.server")


sys.modules[__name__] = _server
