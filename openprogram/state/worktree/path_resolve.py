"""Path resolution helper for worktree-aware tools.

When an active agent worktree is bound to the current context (via
:func:`openprogram.state.worktree.context.set_worktree`), file tools should:

  * resolve a *relative* path against the worktree root,
  * surface a soft warning when an absolute path falls outside the
    worktree (D6 — warn-not-block: the LLM may legitimately need to
    read /etc/passwd or similar; we record the fact so the
    ContextCommit can flag it but don't refuse).

When no worktree is bound, behaviour is unchanged: relative paths
remain an error in the existing tools (caller has to pass absolute).

The single :func:`resolve_path` helper hands back ``(abs_path,
outside_warning)`` — ``outside_warning`` is a non-empty string when
the path is outside the worktree (used by tool wrappers to prepend
``[outside worktree]`` to their reply), or ``None`` when the path
is inside (or no worktree is bound). The string is human-readable,
LLM-friendly, and includes the worktree path for debugging.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from openprogram.state.worktree.context import current_worktree_path


_LOG = logging.getLogger(__name__)


def resolve_path(file_path: str) -> tuple[str, Optional[str]]:
    """Return ``(absolute_path, outside_warning)``.

    Rules:

      * No worktree bound + relative path → returns the path as-is
        with no warning. The caller's existing absolute-path check
        will reject it. (Backwards-compatible: existing tools refuse
        relative paths when no worktree context exists.)
      * Worktree bound + relative path → resolve against worktree
        root, no warning.
      * Worktree bound + absolute path inside worktree → no rewrite,
        no warning.
      * Worktree bound + absolute path *outside* worktree → no
        rewrite, warning string returned.
    """
    wt = current_worktree_path()
    if not wt:
        return file_path, None
    try:
        wt_real = os.path.realpath(wt)
    except Exception:
        wt_real = wt

    if not os.path.isabs(file_path):
        # Resolve relative to worktree root.
        resolved = os.path.normpath(os.path.join(wt_real, file_path))
        # If the LLM passed something like ``../foo`` that escapes the
        # worktree, flag as outside. We still return the resolved path
        # so the operation can proceed (warn-not-block).
        try:
            Path(resolved).resolve().relative_to(Path(wt_real).resolve())
            return resolved, None
        except ValueError:
            return resolved, (
                f"[outside worktree] resolved path {resolved!r} escapes "
                f"the active worktree {wt!r}; proceeding anyway."
            )
        except Exception:
            return resolved, None

    # Absolute path: check containment.
    try:
        Path(file_path).resolve().relative_to(Path(wt_real).resolve())
        return file_path, None
    except ValueError:
        msg = (
            f"[outside worktree] {file_path!r} is outside the active "
            f"worktree {wt!r}; proceeding anyway."
        )
        _LOG.warning(msg)
        return file_path, msg
    except Exception:
        return file_path, None


def default_root() -> str:
    """The folder a file tool searches when it is given no path: the
    bound worktree (or the folder the turn runs in), else the default
    workdir, else the process cwd."""
    wt = current_worktree_path()
    if wt:
        return wt
    try:
        from openprogram.state.paths import get_default_workdir
        return str(get_default_workdir())
    except Exception:
        return os.getcwd()


def resolve_root(path: Optional[str]) -> tuple[str, Optional[str]]:
    """``(directory, outside_warning)`` for a search tool's ``path``
    argument: omitted means :func:`default_root`; relative means relative
    to the bound worktree (left relative when none is bound, so the
    tool's own absolute-path check still answers)."""
    if not path:
        return default_root(), None
    return resolve_path(path)


__all__ = ["resolve_path", "resolve_root", "default_root"]
