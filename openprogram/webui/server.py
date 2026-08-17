"""Compatibility alias for :mod:`openprogram_server.server`.

Server application assembly is owned by ``apps/server``. This module preserves
the established import path without creating a second set of server globals.
"""

from importlib import import_module, util
from pathlib import Path
import sys


def _load_checkout_package(package_dir: Path):
    """Load this checkout's Server package instead of an older installation."""
    existing = sys.modules.get("openprogram_server")
    existing_file = getattr(existing, "__file__", None)
    if existing is not None:
        if existing_file is None:
            raise ImportError(
                "openprogram_server was already imported from an unknown location"
            )
        try:
            if Path(existing_file).resolve().is_relative_to(package_dir.resolve()):
                return existing
        except OSError:
            pass
        raise ImportError(
            "openprogram_server was already imported from a different location: "
            f"{existing_file}"
        )
    _spec = util.spec_from_file_location(
        "openprogram_server",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if _spec is None or _spec.loader is None:
        raise ImportError(f"cannot load OpenProgram Server package from {package_dir}")
    _package = util.module_from_spec(_spec)
    sys.modules["openprogram_server"] = _package
    _spec.loader.exec_module(_package)
    return _package


_checkout_package_dir = (
    Path(__file__).resolve().parents[2] / "apps/server/openprogram_server"
)
if (_checkout_package_dir / "__init__.py").is_file():
    _load_checkout_package(_checkout_package_dir)

_server = import_module("openprogram_server.server")


sys.modules[__name__] = _server
