"""send_file function — self-registers via @function on import."""
from . import send_file as send_file_mod
from .send_file import (
    MAX_SEND_BYTES,
    _send_file_impl,
    begin_turn,
    drain,
    markers_for,
    send_file,
)

__all__ = ["send_file", "send_file_mod", "_send_file_impl", "begin_turn",
           "drain", "markers_for", "MAX_SEND_BYTES"]
