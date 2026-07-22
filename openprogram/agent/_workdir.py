"""Default chat-runtime workdir resolution — re-export shim.

The implementation lives in ``openprogram.agent.internals._workdir``.
This module used to be a byte-identical copy; keeping two copies let
them drift, so it now just re-exports the real one.
"""
from openprogram.agent.internals._workdir import (  # noqa: F401
    apply_default_workdir,
    project_workdir_for,
    session_workdir_for,
)
