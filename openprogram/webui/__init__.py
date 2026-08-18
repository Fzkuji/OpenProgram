"""Compatibility namespace for the OpenProgram Server application.

Server source lives in :mod:`openprogram_server`.  This package preserves the
established ``openprogram.webui.*`` module names while loading their single
implementation from the Server application package.

Usage:
    from openprogram.webui import start_web
    start_web(port=18100)

Or from CLI:
    openprogram web
    python -m openprogram.webui
"""

from importlib import util
from pathlib import Path
import sys


def _load_server_package():
    """Resolve this checkout's Server package, rejecting mixed installations."""
    from openprogram.updater.detect import repo_root

    checkout = repo_root()
    package_dir = (
        checkout / "apps/server/openprogram_server" if checkout is not None else None
    )
    existing = sys.modules.get("openprogram_server")
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file is None:
            raise ImportError(
                "openprogram_server was already imported from an unknown location"
            )
        try:
            if package_dir is not None and Path(existing_file).resolve().is_relative_to(
                package_dir.resolve()
            ):
                return existing
        except OSError:
            pass
        if package_dir is not None and package_dir.is_dir():
            raise ImportError(
                "openprogram_server was already imported from a different location: "
                f"{existing_file}"
            )
        return existing

    if package_dir is None or not (package_dir / "__init__.py").is_file():
        from importlib import import_module

        return import_module("openprogram_server")

    spec = util.spec_from_file_location(
        "openprogram_server",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load OpenProgram Server package from {package_dir}")
    package = util.module_from_spec(spec)
    sys.modules["openprogram_server"] = package
    spec.loader.exec_module(package)
    return package


_server_package = _load_server_package()
_implementation_dir = Path(_server_package.__file__).resolve().parent / "_webui"
if not _implementation_dir.is_dir():
    raise ImportError(f"OpenProgram Server implementation is missing: {_implementation_dir}")
if str(_implementation_dir) not in __path__:
    __path__.append(str(_implementation_dir))

# ponytail: PEP 562 lazy export instead of an eager
# ``from openprogram_server.server import ...``. The eager form made merely
# *touching* any submodule of this package (e.g. the models.dev base-url
# lookup in providers/metadata.py) drag in webui.server, which imports
# functions.agentics.ask_user, which fires the whole agentic registry load
# while openprogram.programs._runtime is still mid-init — breaking every
# harness whose @agentic_function runs at module scope.
def __getattr__(name):
    if name in ("start_server", "stop_server"):
        from openprogram.webui import server
        return getattr(server, name)
    raise AttributeError(name)


def start_web(port: int = 18100, open_browser: bool = False):
    """
    Start the web UI server in a background thread.

    Opens a browser window showing the execution tree. Updates in real-time
    as @agentic_function calls are made.

    Args:
        port: Port to serve on (default 18100).
        open_browser: Whether to open a browser tab automatically.

    Returns:
        The background thread running the server.
    """
    from openprogram.webui.server import start_server
    return start_server(port=port, open_browser=open_browser)


# Backward-compatible alias
start_visualizer = start_web

__all__ = ["start_web", "start_visualizer", "start_server", "stop_server"]
