"""canvas tool — incremental append / overwrite of named blocks in a markdown file.

A canvas is a single markdown file where the agent can stash structured
output across many turns without rewriting the whole file each time. Each
block is delimited by HTML comments so the file remains valid markdown
and renders in any viewer:

    <!-- canvas:block id="summary" -->
    ... content ...
    <!-- canvas:endblock -->

Supported actions:

  set     overwrite the named block (creates it if absent)
  append  append to the named block (creates it if absent)
  get     return the block's current content
  list    return all block ids + their byte sizes
  delete  remove the block entirely

Default canvas path is ``$OPENPROGRAM_CANVAS_PATH`` if set, else
``./canvas.md`` in the process cwd. Callers can pass ``path=...`` per
call to use multiple canvases.

Credit: design inspired by OpenClaw's canvas/MCP canvas — simplified to a
single flat file (no nested documents, no revision history). When a
richer canvas UI lands in the WebUI we can keep the same wire format
and just add rendering.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..._helpers import read_string_param
from ..._runtime import function


NAME = "canvas"

DEFAULT_CANVAS_ENV = "OPENPROGRAM_CANVAS_PATH"
DEFAULT_FILENAME = "canvas.md"

DESCRIPTION = (
    "Write named blocks to a persistent canvas file (default `./canvas.md`). "
    "Each block has an id; calling set/append with the same id edits that "
    "block in place without rewriting the rest of the file. Use for "
    "streaming report-style output where later turns refine earlier sections."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "append", "get", "list", "delete"],
                "description": "What to do with the canvas.",
            },
            "block_id": {
                "type": "string",
                "description": "Block identifier. Required for set/append/get/delete.",
            },
            "content": {
                "type": "string",
                "description": "Block content. Required for set/append.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Absolute or relative path to the canvas file. Default: "
                    f"${DEFAULT_CANVAS_ENV} or ./{DEFAULT_FILENAME}."
                ),
            },
        },
        "required": ["action"],
    },
}


_BLOCK_RE = re.compile(
    r"<!-- canvas:block id=\"(?P<id>[^\"]+)\" -->\n?"
    r"(?P<body>.*?)"
    r"\n?<!-- canvas:endblock -->",
    re.DOTALL,
)


def _resolve_path(path: str | None) -> str:
    if path:
        return os.path.abspath(path)
    env = os.environ.get(DEFAULT_CANVAS_ENV)
    if env:
        return os.path.abspath(env)
    return os.path.abspath(DEFAULT_FILENAME)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _find(text: str, block_id: str) -> re.Match[str] | None:
    for m in _BLOCK_RE.finditer(text):
        if m.group("id") == block_id:
            return m
    return None


def _format_block(block_id: str, body: str) -> str:
    body = body.rstrip("\n")
    return f'<!-- canvas:block id="{block_id}" -->\n{body}\n<!-- canvas:endblock -->'


def _valid_id(block_id: str) -> bool:
    # Keep ids friendly — matches most slug conventions; disallow quotes
    # that would break the HTML-comment delimiter.
    return bool(block_id) and all(ch not in block_id for ch in '"\n<>')


def execute(
    action: str | None = None,
    block_id: str | None = None,
    content: str | None = None,
    path: str | None = None,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op", "mode")
    block_id = block_id or read_string_param(kw, "block_id", "blockId", "id", "name")
    content = content or read_string_param(kw, "content", "body", "text")
    path_arg = path or read_string_param(kw, "path", "file", "canvas_path")

    if not action:
        return "Error: `action` is required (one of set/append/get/list/delete)."
    action = action.lower()
    if action not in {"set", "append", "get", "list", "delete"}:
        return f"Error: unknown action {action!r}. Expected set/append/get/list/delete."

    resolved = _resolve_path(path_arg)
    current = _read(resolved)

    if action == "list":
        rows = [
            f"- `{m.group('id')}` ({len(m.group('body'))} chars)"
            for m in _BLOCK_RE.finditer(current)
        ]
        if not rows:
            return f"Canvas `{resolved}` is empty (no blocks)."
        return f"Blocks in `{resolved}`:\n" + "\n".join(rows)

    if not block_id:
        return f"Error: `block_id` is required for action {action!r}."
    if not _valid_id(block_id):
        return f"Error: invalid block id {block_id!r} (no quotes, newlines, or angle brackets)."

    existing = _find(current, block_id)

    if action == "get":
        if not existing:
            return f"Error: block {block_id!r} not found in `{resolved}`."
        return existing.group("body")

    if action == "delete":
        if not existing:
            return f"Error: block {block_id!r} not found in `{resolved}`."
        start, end = existing.span()
        new_text = current[:start] + current[end:]
        # Tidy surrounding blank lines left behind by the removal.
        new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip() + "\n"
        _write(resolved, new_text)
        return f"Deleted block {block_id!r} from `{resolved}`."

    # set / append need content
    if content is None:
        return f"Error: `content` is required for action {action!r}."

    if action == "set":
        new_block = _format_block(block_id, content)
        if existing:
            start, end = existing.span()
            new_text = current[:start] + new_block + current[end:]
        else:
            new_text = (current.rstrip("\n") + "\n\n" + new_block + "\n") if current else (new_block + "\n")
        _write(resolved, new_text)
        return f"Set block {block_id!r} in `{resolved}` ({len(content)} chars)."

    # append
    if existing:
        merged_body = existing.group("body") + "\n" + content
        new_block = _format_block(block_id, merged_body)
        start, end = existing.span()
        new_text = current[:start] + new_block + current[end:]
    else:
        new_block = _format_block(block_id, content)
        new_text = (current.rstrip("\n") + "\n\n" + new_block + "\n") if current else (new_block + "\n")
    _write(resolved, new_text)
    return f"Appended to block {block_id!r} in `{resolved}` (+{len(content)} chars)."



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]
