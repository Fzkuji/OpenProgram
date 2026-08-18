"""Installed through PYTHONPATH only for local agent child interpreters."""
import os

if (
    os.environ.get("OPENPROGRAM_RECOVERABLE_TRASH")
    and os.environ.get("OPENPROGRAM_DELETE_HELPER") != "1"
):
    from openprogram.sandbox.recoverable_delete import install_python_shims

    install_python_shims()

