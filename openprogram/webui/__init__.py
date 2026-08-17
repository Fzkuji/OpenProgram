"""
OpenProgram Web API and static-interface host.

This package exposes the worker's HTTP and WebSocket surface and serves the
prebuilt Next.js export. Core runtime modules do not eagerly import the server;
the CLI and worker load it only when the Web service is requested.

Usage:
    from openprogram.webui import start_web
    start_web(port=18100)

Or from CLI:
    openprogram web
    python -m openprogram.webui
"""

# ponytail: PEP 562 lazy export instead of an eager
# ``from openprogram.webui.server import ...``. The eager form made merely
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
