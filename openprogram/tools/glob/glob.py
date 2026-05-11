"""glob tool — find files by name pattern."""

from __future__ import annotations

import glob as _glob
import os

from openprogram.tools._runtime import to_dict_tool, tool


_DESCRIPTION = (
    "Find files matching a glob pattern (like `**/*.py`), sorted by "
    "modification time (newest first).\n"
    "\n"
    "- Use this instead of `find` or `ls` for file discovery.\n"
    "- Supports recursive `**` and standard glob syntax.\n"
    "- `path` defaults to cwd.\n"
    "- ALWAYS scope `path` to the smallest known project / target "
    "root. Do not glob `$HOME` or `/` — recursive `**` over a home "
    "directory walks tens of thousands of files and stalls the turn."
)


def _is_dangerous_root(root: str) -> bool:
    """Return True if `root` is a directory whose recursive walk is
    almost never what the caller wanted (home, filesystem root,
    common parent dirs that hold everything). Cheap pre-check we run
    before invoking `_glob.glob` with a `**` pattern."""
    try:
        rp = os.path.realpath(root).rstrip("/")
    except Exception:
        return False
    if rp in ("", "/"):
        return True
    home = os.path.expanduser("~").rstrip("/")
    if home and rp == home:
        return True
    return False


@tool(
    name="glob",
    description=_DESCRIPTION,
    toolset=["core", "research"],
)
def glob_tool(pattern: str, path: str | None = None) -> str:
    """Find files matching a glob pattern.

    Args:
        pattern: Glob pattern, e.g. "**/*.py" or "src/*.ts".
        path: Directory to search in. Absolute path. Defaults to cwd.
    """
    if path:
        root = path
    else:
        try:
            from openprogram.paths import get_default_workdir
            root = get_default_workdir()
        except Exception:
            root = os.getcwd()
    if not os.path.isabs(root):
        return f"Error: path must be absolute, got {root!r}"
    if not os.path.isdir(root):
        return f"Error: not a directory: {root}"

    # Hard guard: refuse `**`-style recursive walks rooted at $HOME or
    # "/". The LLM occasionally targets these when it has no project
    # root in context; the walk takes minutes, blocks the turn, and
    # produces useless 50k-entry result lists. Force the caller to
    # pick a narrower root instead of silently doing the slow thing.
    if "**" in pattern and _is_dangerous_root(root):
        return (
            f"Error: refusing recursive glob over {root!r}. "
            "Scope `path` to the specific project / directory you "
            "want to search (e.g. the repository root) instead of "
            "$HOME or `/`."
        )

    full_pattern = os.path.join(root, pattern)
    matches = _glob.glob(full_pattern, recursive=True)
    matches = [m for m in matches if os.path.isfile(m)]
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    if not matches:
        return f"No matches for {pattern!r} under {root}"
    if len(matches) > 500:
        return f"# {len(matches)} matches (showing 500 newest)\n" + "\n".join(matches[:500])
    return f"# {len(matches)} matches\n" + "\n".join(matches)


GLOB = glob_tool
NAME = GLOB.name
DESCRIPTION = _DESCRIPTION
_LEGACY = to_dict_tool(GLOB)
SPEC = _LEGACY["spec"]
execute = _LEGACY["execute"]
