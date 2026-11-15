"""Which commit an installed build was made from.

A build made by the evaluation launcher (``scripts/benchmarks/frontier_eval.py``)
carries ``openprogram/_build_info.py`` with the commit it was archived from
and when it was built, written into the source tree before ``uv build``, as
Claude Code injects its version and build time at build time. A source
checkout has no such file (it is in ``.gitignore``), so everything here is
``None`` there and development runs as before.

``--version`` and the ``run`` record of a ``--print`` transcript read it, so
a trial's evidence names its build on its own, without the launcher's
``run.json`` beside it.
"""
from __future__ import annotations

from typing import Any

#: The module the launcher writes, relative to the ``openprogram`` package.
MODULE = "openprogram._build_info"


def build_info() -> dict[str, Any]:
    """``{"commit": ..., "built_at": ...}``; both ``None`` without the file."""
    import importlib

    try:
        module = importlib.import_module(MODULE)
    except ImportError:
        return {"commit": None, "built_at": None}
    commit = getattr(module, "COMMIT", None)
    built_at = getattr(module, "BUILT_AT", None)
    return {
        "commit": commit if isinstance(commit, str) and commit else None,
        "built_at": built_at if isinstance(built_at, str) and built_at else None,
    }


def version_text(package_version: str) -> str:
    """What ``openprogram --version`` prints after the program name: the
    package version, and the commit when the build names one."""
    commit = build_info()["commit"]
    return f"{package_version} ({commit[:12]})" if commit else package_version


def module_source(commit: str, built_at: str) -> str:
    """The text of ``openprogram/_build_info.py`` for a build."""
    return (
        '"""Written at build time by the evaluation launcher; not in the repository."""\n'
        f"COMMIT = {commit!r}\n"
        f"BUILT_AT = {built_at!r}\n"
    )


__all__ = ["MODULE", "build_info", "module_source", "version_text"]
