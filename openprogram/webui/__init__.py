"""
agentic_web — real-time web UI for Agentic Programming.

Top-level package, decoupled from the `agentic` framework core. Depends on
agentic (framework) one-way; nothing in agentic imports from openprogram.webui
except via lazy imports in the CLI.

Usage:
    from openprogram.webui import start_web
    start_web(port=8765)

Or from CLI:
    agentic web
    python -m agentic_web
"""


def start_server(*args, **kwargs):
    from openprogram.webui.server import start_server as _start_server

    return _start_server(*args, **kwargs)



def stop_server(*args, **kwargs):
    from openprogram.webui.server import stop_server as _stop_server

    return _stop_server(*args, **kwargs)



def start_web(port: int = 8765, open_browser: bool = True):
    """
    Start the web UI server in a background thread.

    Opens a browser window showing the execution tree. Updates in real-time
    as @agentic_function calls are made.

    Args:
        port: Port to serve on (default 8765).
        open_browser: Whether to open a browser tab automatically.

    Returns:
        The background thread running the server.
    """
    return start_server(port=port, open_browser=open_browser)


# Backward-compatible alias
start_visualizer = start_web

__all__ = ["start_web", "start_visualizer", "start_server", "stop_server"]
