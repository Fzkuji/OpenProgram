"""The two never-writable paths, resolved without importing anything heavy.

The sandbox used to reach into ``openprogram.programs`` for these, which
dragged the whole tool registry into every policy construction — one
unrelated tool failing to import took the sandbox down with it. Both paths
only depend on where the ``openprogram`` package sits and on ``paths``, so
they live here and both sides import this instead.
"""
from __future__ import annotations

import os

PROGRAM_SOURCES_FILE = "program-sources.json"


def applications_root() -> str:
    """Absolute path to ``openprogram/programs/applications``.

    Computed from the top-level package so it works for editable and
    site-packages installs, and without importing the agentics package
    (which would recurse during its own load).
    """
    import openprogram
    return os.path.join(
        os.path.dirname(os.path.abspath(openprogram.__file__)),
        "programs", "applications",
    )


def program_sources_path() -> str:
    from openprogram import paths
    return str(paths.get_state_dir() / PROGRAM_SOURCES_FILE)
