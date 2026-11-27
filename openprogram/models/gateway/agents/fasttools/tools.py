"""The tools' list and schemas, after magpie's ``internal/fasttools/tools.go``
(MIT License, Copyright (c) yetone)."""
from __future__ import annotations

from typing import Any

# SERVER_NAME is the MCP server's name in the plugin; agy names its tools
# mcp_<plugin>_<server>_<tool>.
SERVER_NAME = "fast"

# INSTRUCTIONS go to the model with the server (initialize). They say what
# the tools are for, and nothing of who wrote them or of the agent's own
# tools' limits: the text reaches the model's vendor.
INSTRUCTIONS = "Batch work into few calls. batch_search runs up to 20 searches (text, regex, file globs, read-only git) in one call. read_slices gives outlines and line ranges of several files at once. apply_edits makes every edit of a change across files in one call, checks them all before writing any, and can run a verify command after. run_checks runs independent build, test and lint commands side by side. Pass cwd, the workspace's absolute path, on every call."

# TOOL_NAMES are the tools, in the order they're listed.
TOOL_NAMES = ["batch_search", "read_slices", "apply_edits", "run_checks"]


def _obj(props: dict[str, Any], *required: str) -> dict[str, Any]:
    o: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        o["required"] = list(required)
    return o


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _num(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


def _flag(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}


_CWD_PROP = _str("The workspace's absolute path; relative paths are read from it. Defaults to the server's working folder.")


def tool_list() -> list[Any]:
    from .checks import MAX_CHECKS
    from .edits import MAX_EDITS
    from .search import MAX_QUERIES
    from .slices import MAX_FILES

    return [
        {
            "name": "batch_search",
            "description": "Run several independent searches in one call: text or regex over file contents, globs over file names, and read-only git commands. Use it instead of one search per turn; put every search you need next into one call.",
            "inputSchema": _obj({
                "cwd": _CWD_PROP,
                "queries": {
                    "type": "array", "maxItems": MAX_QUERIES,
                    "description": "Up to 20 searches, run side by side.",
                    "items": _obj({
                        # magpie says RE2 here; the regexes are Python's re,
                        # which reads RE2's everyday syntax alike
                        "kind": {"type": "string", "enum": ["text", "regex", "glob", "git"], "description": "text: a literal string in contents; regex: a regular expression (Python re syntax) in contents; glob: file names (** for any folders); git: a read-only git command's arguments (status, diff, log, show, blame, ls-files, grep, branch, rev-parse)."},
                        "pattern": _str("The text, regex, glob, or git arguments."),
                        "path": _str("Only under this folder or file (relative to cwd)."),
                        "glob": _str("Only files whose path matches this glob, e.g. **/*.go."),
                        "ignoreCase": _flag("Match case-insensitively (text and regex)."),
                        "context": _num("Lines of context around each match (0 to 5)."),
                        "max": _num("Most matches to give (default 40, at most 200)."),
                    }, "kind", "pattern"),
                },
            }, "queries"),
        },
        {
            "name": "read_slices",
            "description": "Read several files at once: an outline of each one's declarations (functions, types, classes, headings) with line numbers, and the line ranges you ask for. A file asked for with no ranges comes whole when short, as an outline when long. Use it instead of reading files one by one.",
            "inputSchema": _obj({
                "cwd": _CWD_PROP,
                "files": {
                    "type": "array", "maxItems": MAX_FILES,
                    "items": _obj({
                        "path": _str("The file (relative to cwd)."),
                        "ranges": {"type": "array", "description": "Line ranges to read, each [start, end], 1-based and inclusive.", "items": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}},
                        "outline": _flag("Give the outline (also when ranges are asked for)."),
                    }, "path"),
                },
            }, "files"),
        },
        {
            "name": "apply_edits",
            "description": "Make every edit of a change in one call, across one or more files. Each edit replaces old (which must appear exactly once, unless replaceAll) with new, or writes a whole file from content. Every edit is checked first: when any can't be made, none is written (unless bestEffort), and the reason says where old nearly matched. verify runs a command after writing, such as a build or a test, and gives its result.",
            "inputSchema": _obj({
                "cwd": _CWD_PROP,
                "edits": {
                    "type": "array", "maxItems": MAX_EDITS,
                    "description": "Applied in order; several edits of one file see the earlier ones' result.",
                    "items": _obj({
                        "path": _str("The file (relative to cwd)."),
                        "old": _str("The exact text to replace, with enough lines around it to be unique."),
                        "new": _str("What it becomes."),
                        "replaceAll": _flag("Replace every occurrence of old."),
                        "content": _str("Write the whole file with this text instead (creates it, and its folders)."),
                    }, "path"),
                },
                "bestEffort": _flag("Write the edits that can be made even when others can't."),
                "dryRun": _flag("Check the edits and say what they'd change, writing nothing."),
                "verify": _str("A shell command run in cwd after writing, e.g. go build ./... or npm test."),
                "verifyTimeout": _num("Seconds verify may take (default 120, at most 900)."),
            }, "edits"),
        },
        {
            "name": "run_checks",
            "description": "Run independent commands side by side, such as a build, tests and a linter, and give each one's result: one line when it passes, the end of its output when it fails. Use it instead of running them one per turn.",
            "inputSchema": _obj({
                "cwd": _CWD_PROP,
                "commands": {
                    "type": "array", "maxItems": MAX_CHECKS,
                    "items": _obj({
                        "cmd": _str("The shell command."),
                        "name": _str("A short name for it in the result."),
                        "timeout": _num("Seconds it may take (default 300, at most 1800)."),
                    }, "cmd"),
                },
            }, "commands"),
        },
    ]
