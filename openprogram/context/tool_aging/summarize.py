"""One-line semantic stubs for aged tool results.

When an older turn's tool result is too verbose to keep, we replace
the full text with a single line that still tells the model:

  * which tool ran
  * with what (truncated) args
  * what the rough outcome was (line count / exit code / error /
    first 80 chars of output)

The model gets enough signal to remember "I called read_file on
foo.py, it returned ~120 lines" without ballooning context.
Inspired by Hermes' ``_summarize_tool_result`` (references/hermes-agent/
agent/context_compressor.py).
"""
from __future__ import annotations

import json
import re

from .policy import MAX_TOOL_ARGS_CHARS, STUB_PREFIX


def _shrink_args(args) -> str:
    """JSON-stringify args, cap at ``MAX_TOOL_ARGS_CHARS``.

    Tool args are usually small (paths, queries, ids), but a tool
    that takes a giant blob as input (e.g. apply_patch with a
    multi-file diff) would otherwise dominate the stub.
    """
    if args in (None, "", {}, []):
        return ""
    if not isinstance(args, str):
        try:
            args = json.dumps(args, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            args = repr(args)
    if len(args) > MAX_TOOL_ARGS_CHARS:
        args = args[: MAX_TOOL_ARGS_CHARS - 1] + "…"
    return args


def _outcome_blurb(result: str, is_error: bool) -> str:
    """Pick the most informative one-liner from a tool's output.

    Heuristics:
      * is_error=True → "error: <first 80 chars>"
      * starts with "# " or absolute path → first line (likely a file
        header from read / search tools)
      * has line-count signal ("X matches", "Y files", "N rows") →
        carry it verbatim
      * otherwise → "N lines, first: <head>"
    """
    if not isinstance(result, str):
        result = str(result)
    result = result.strip()
    if not result:
        return "(empty)"
    if is_error:
        return f"error: {result[:80]}"
    first_line = result.splitlines()[0][:120]
    # Tools like grep/glob/list_files commonly summarize with these
    # patterns at the top — keep them verbatim.
    if re.search(r"\b\d+\s+(matches?|files?|rows?|results?)\b", first_line, re.I):
        return first_line
    if first_line.startswith(("# ", "/", "├", "└")):
        # File header or tree row.
        return first_line
    lines = result.count("\n") + 1
    return f"{lines} lines, first: {first_line[:80]}"


def summarize_tool_call(name: str, args, result, is_error: bool) -> str:
    """Build the aged-stub string for one tool call."""
    arg_str = _shrink_args(args)
    blurb = _outcome_blurb(result, is_error)
    if arg_str:
        return f"{STUB_PREFIX} {name}({arg_str}) → {blurb}"
    return f"{STUB_PREFIX} {name} → {blurb}"
