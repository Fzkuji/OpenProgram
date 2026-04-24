"""apply_patch tool — multi-file structured patches in Codex / OpenClaw format.

Format::

    *** Begin Patch
    *** Add File: /abs/path/new.py
    +line one
    +line two
    *** Update File: /abs/path/existing.py
    @@ optional context line
     unchanged context
    -old line
    +new line
    @@ another hunk
    -other old
    +other new
    *** Delete File: /abs/path/gone.py
    *** End Patch

Rules:
- All paths must be absolute.
- ``Update File`` blocks contain one or more ``@@`` hunks. Within a hunk:
    prefix ``-`` = line to remove
    prefix ``+`` = line to add
    prefix `` `` = context (must match file)
- A hunk's "before" text (context + ``-`` lines, in order) must appear
  contiguously in the target file exactly once per hunk application.
"""

from __future__ import annotations

import os
from typing import Any


NAME = "apply_patch"

DESCRIPTION = (
    "Apply a structured multi-file patch (Add / Update / Delete). Use for "
    "edits that span multiple files or multiple locations. For a single exact "
    "replacement use `edit` instead; for creating/overwriting one file use `write`.\n"
    "\n"
    "Patch envelope:\n"
    "  *** Begin Patch\n"
    "  *** Update File: /absolute/path.py\n"
    "  @@ context\n"
    "   unchanged\n"
    "  -old\n"
    "  +new\n"
    "  *** End Patch\n"
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "Full patch text including *** Begin Patch / *** End Patch markers.",
            },
        },
        "required": ["patch"],
    },
}


def _parse_sections(patch: str) -> list[tuple[str, str, list[str]]]:
    """Return a list of (op, path, body_lines) tuples."""
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("patch must start with '*** Begin Patch'")
    if lines[-1].strip() != "*** End Patch":
        raise ValueError("patch must end with '*** End Patch'")
    body = lines[1:-1]

    sections: list[tuple[str, str, list[str]]] = []
    cur_op: str | None = None
    cur_path: str | None = None
    cur_body: list[str] = []

    def flush() -> None:
        if cur_op is not None:
            sections.append((cur_op, cur_path or "", cur_body[:]))

    for ln in body:
        if ln.startswith("*** Add File: "):
            flush()
            cur_op, cur_path, cur_body[:] = "add", ln[len("*** Add File: "):].strip(), []
        elif ln.startswith("*** Update File: "):
            flush()
            cur_op, cur_path, cur_body[:] = "update", ln[len("*** Update File: "):].strip(), []
        elif ln.startswith("*** Delete File: "):
            flush()
            cur_op, cur_path, cur_body[:] = "delete", ln[len("*** Delete File: "):].strip(), []
        else:
            if cur_op is None:
                continue  # blank leading line
            cur_body.append(ln)
    flush()
    return sections


def _apply_add(path: str, body: list[str]) -> str:
    if os.path.exists(path):
        return f"Error: Add File target already exists: {path}"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    content = "\n".join(l[1:] if l.startswith("+") else l for l in body)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + ("\n" if not content.endswith("\n") else ""))
    return f"Added {path} ({len(body)} lines)"


def _apply_delete(path: str) -> str:
    if not os.path.exists(path):
        return f"Error: Delete File target not found: {path}"
    os.remove(path)
    return f"Deleted {path}"


def _apply_update(path: str, body: list[str]) -> str:
    if not os.path.exists(path):
        return f"Error: Update File target not found: {path}"

    # Split body into hunks on lines starting with "@@". The text after "@@"
    # (context hint) is informational only — we use the +/- lines to locate.
    hunks: list[list[str]] = []
    current: list[str] = []
    started = False
    for ln in body:
        if ln.startswith("@@"):
            if started and current:
                hunks.append(current)
            current = []
            started = True
            continue
        if started:
            current.append(ln)
    if started and current:
        hunks.append(current)
    if not started:
        # no @@ marker — treat the whole body as one implicit hunk
        hunks = [body]

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    applied = 0
    for idx, hunk in enumerate(hunks):
        before_lines: list[str] = []
        after_lines: list[str] = []
        for ln in hunk:
            if not ln:
                before_lines.append("")
                after_lines.append("")
                continue
            tag, rest = ln[0], ln[1:]
            if tag == " ":
                before_lines.append(rest)
                after_lines.append(rest)
            elif tag == "-":
                before_lines.append(rest)
            elif tag == "+":
                after_lines.append(rest)
            else:
                # stray marker / comment — ignore
                pass

        before = "\n".join(before_lines)
        after = "\n".join(after_lines)
        if not before:
            return f"Error: hunk #{idx + 1} in {path} has no context or removal lines"

        count = text.count(before)
        if count == 0:
            return f"Error: hunk #{idx + 1} not found in {path}"
        if count > 1:
            return (
                f"Error: hunk #{idx + 1} matches {count} locations in {path}; "
                "add more context so the match is unique"
            )
        text = text.replace(before, after, 1)
        applied += 1

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return f"Updated {path} ({applied} hunk{'s' if applied != 1 else ''})"


def execute(patch: str, **_: Any) -> str:
    try:
        sections = _parse_sections(patch)
    except ValueError as e:
        return f"Error parsing patch: {e}"
    if not sections:
        return "Error: patch contains no file operations"

    results: list[str] = []
    for op, path, body in sections:
        if not os.path.isabs(path):
            results.append(f"Error: path must be absolute: {path}")
            continue
        try:
            if op == "add":
                results.append(_apply_add(path, body))
            elif op == "update":
                results.append(_apply_update(path, body))
            elif op == "delete":
                results.append(_apply_delete(path))
            else:
                results.append(f"Error: unknown op {op!r} for {path}")
        except Exception as e:
            results.append(f"Error applying {op} to {path}: {type(e).__name__}: {e}")
    return "\n".join(results)
