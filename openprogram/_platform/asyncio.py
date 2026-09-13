"""Platform asyncio."""
from __future__ import annotations
from .. import _compat as state


def install_asyncio_exception_handler(loop) -> None:
    """Install the small set of platform-specific asyncio workarounds.

    On Windows, CPython's proactor transport can report a peer reset from
    ``_call_connection_lost`` *after* the WebSocket has already completed its
    normal disconnect path.  The callback runs outside application code, so
    catching ``WebSocketDisconnect`` cannot prevent the noisy ``WinError
    10054`` traceback.  Suppress only that exact transport-teardown callback;
    every other loop exception keeps the caller's existing/default handling.
    """

    previous = loop.get_exception_handler()

    def _handler(active_loop, context) -> None:
        exception = context.get("exception")
        handle = context.get("handle")
        callback = getattr(handle, "_callback", None)
        owner = getattr(callback, "__self__", None)
        callback_name = getattr(callback, "__name__", "")
        owner_module = getattr(type(owner), "__module__", "")
        winerror = getattr(exception, "winerror", None)
        benign_windows_disconnect = (
            state._sys.platform == "win32"
            and isinstance(exception, ConnectionResetError)
            and winerror == 10054
            and callback_name == "_call_connection_lost"
            and owner_module == "asyncio.proactor_events"
        )
        if benign_windows_disconnect:
            return
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
