"""OpenProgram Server application package.

The package assembles the HTTP, WebSocket, and static Web surfaces around the
reusable :mod:`openprogram` Agent Core. Importing the package does not start a
listener or initialize providers.
"""


def __getattr__(name):
    if name in {"create_app", "start_server", "stop_server"}:
        from openprogram_server import server

        return getattr(server, name)
    raise AttributeError(name)


def start_web(port: int = 18100, open_browser: bool = False):
    """Start the Server application in a background thread."""
    from openprogram_server.server import start_server

    return start_server(port=port, open_browser=open_browser)


__all__ = ["create_app", "start_web", "start_server", "stop_server"]
