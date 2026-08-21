"""grep function — ripgrep-powered content search (falls back to Python re if rg missing)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from openprogram.programs._runtime import function


_DESCRIPTION = (
    "Search file contents for a regular expression. Uses ripgrep when "
    "available, falls back to a Python regex walker otherwise.\n"
    "\n"
    "- Pattern is a standard regex (ripgrep flavor when rg is available).\n"
    "- `path` defaults to cwd; absolute paths recommended.\n"
    "- Output modes: files_with_matches (default), content, count.\n"
    "- Use `glob` for pure filename matching."
)


def _run_rg(pattern: str, path: str, glob: str | None,
            output_mode: str, case_insensitive: bool) -> str:
    cmd = ["rg", "--no-heading"]
    if case_insensitive:
        cmd.append("-i")
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    else:
        cmd.extend(["-n", "-H"])
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend(["--", pattern, path])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode not in (0, 1):
        return f"Error: rg exited {proc.returncode}: {proc.stderr.strip()}"
    out = _drop_denied_lines(proc.stdout.rstrip())
    return out or "No matches"


def _line_path(line: str) -> str:
    """The file path an rg output line starts with.

    Every mode we ask rg for is path-prefixed (``-l`` bare path, ``-c``
    ``path:count``, ``-n -H`` ``path:line:text``), and a path may itself
    contain colons, so take the longest prefix that is an existing file.
    """
    if os.path.exists(line):
        return line
    idx = len(line)
    while True:
        idx = line.rfind(":", 0, idx)
        if idx < 0:
            return line
        head = line[:idx]
        if os.path.exists(head):
            return head


def _drop_denied_lines(out: str) -> str:
    """Remove result lines naming a deny-read path. rg runs in-process
    here (no OS sandbox wraps it), so the policy has no other seam."""
    from openprogram.sandbox import read_denier
    denied = read_denier()
    if denied is None or not out:
        return out
    return "\n".join(
        line for line in out.splitlines() if not denied(_line_path(line))
    )


def _run_python_fallback(pattern: str, path: str, glob: str | None,
                         output_mode: str, case_insensitive: bool) -> str:
    import fnmatch

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    files_with_matches: list[str] = []
    content_lines: list[str] = []
    counts: dict[str, int] = {}

    from openprogram.sandbox import read_denier
    denied = read_denier()

    if os.path.isfile(path):
        candidates = [path]
    else:
        candidates = []
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if glob and not fnmatch.fnmatch(f, glob):
                    continue
                candidates.append(fp)
    if denied is not None:
        candidates = [fp for fp in candidates if not denied(fp)]

    for fp in candidates:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            continue
        n = 0
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                n += 1
                if output_mode == "content":
                    content_lines.append(f"{fp}:{i}:{line}")
        if n:
            files_with_matches.append(fp)
            counts[fp] = n

    if output_mode == "count":
        if not counts:
            return "No matches"
        return "\n".join(f"{fp}:{n}" for fp, n in counts.items())
    if output_mode == "content":
        if not content_lines:
            return "No matches"
        return "\n".join(content_lines[:500])
    if not files_with_matches:
        return "No matches"
    return "\n".join(files_with_matches[:500])


@function(
    name="grep",
    accept_edits_safe=True,   # acceptEdits 档下自动放行（只读）
    description=_DESCRIPTION,
    max_result_chars=20_000,    # Claude Code default for grep
    toolset=["core", "research"],
    path_params={"path": "read"},
)
def grep(pattern: str,
         path: str | None = None,
         glob: str | None = None,
         output_mode: str = "files_with_matches",
         case_insensitive: bool = False) -> str:
    """Search file contents for a regex.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search. Defaults to cwd.
        glob: Optional glob filter (e.g. "*.py").
        output_mode: Output format: "files_with_matches", "content", or "count".
        case_insensitive: Case-insensitive match. Default false.
    """
    if path:
        root = path
    else:
        try:
            from openprogram.paths import get_default_workdir
            root = get_default_workdir()
        except Exception:
            root = os.getcwd()
    if not os.path.exists(root):
        return f"Error: path not found: {root}"
    from openprogram.sandbox import validate_read_path
    violation = validate_read_path(root)
    if violation:
        return f"Error: sandbox policy: {violation}"
    if shutil.which("rg"):
        return _run_rg(pattern, root, glob, output_mode, case_insensitive)
    return _run_python_fallback(pattern, root, glob, output_mode, case_insensitive)
