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

from openprogram.programs._runtime import function
from openprogram.store.snapshot.checkpoint.helpers import (
    checkpoint_abort_edit,
    checkpoint_after_edit,
    checkpoint_before_edit,
)


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


def _emit_file_changed(path: str, op: str) -> None:
    # 事件层 tap：改动成功才调。懒 import 防循环依赖。
    try:
        from openprogram.events import emit_safe
        emit_safe("file.changed", "tool", {"path": path, "op": op})
    except Exception:
        pass


def _apply_add(path: str, body: list[str]) -> str:
    if os.path.exists(path):
        return f"Error: Add File target already exists: {path}"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    content = _add_content(body)
    prepared = checkpoint_before_edit(path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        if prepared:
            checkpoint_abort_edit(path, str(exc))
        raise
    checkpoint_after_edit(path, "add")
    # Baseline the new file so a later Update in the same session doesn't
    # trip the "never read" gate (the agent just created it).
    try:
        from openprogram.store.snapshot import read_tracking as _rt
        _rt.mark_seen(path)
    except Exception:
        pass
    _emit_file_changed(path, "add")
    return f"Added {path} ({len(body)} lines)"


def _apply_delete(path: str) -> str:
    if not os.path.exists(path):
        return f"Error: Delete File target not found: {path}"
    prepared = checkpoint_before_edit(path)
    try:
        os.remove(path)
    except Exception as exc:
        if prepared:
            checkpoint_abort_edit(path, str(exc))
        raise
    checkpoint_after_edit(path, "delete")
    _emit_file_changed(path, "delete")
    return f"Deleted {path}"


def _replace_line_span(
    text: str, before_lines: list[str], after_lines: list[str],
) -> tuple[int, str]:
    """Replace one consecutive line sequence. Mid-line substrings do not match."""
    lines = text.splitlines()
    trailing = text.endswith("\n")
    n = len(before_lines)
    if n == 0:
        return 0, text
    hits = [i for i in range(len(lines) - n + 1) if lines[i:i + n] == before_lines]
    if len(hits) != 1:
        return len(hits), text
    i = hits[0]
    lines[i:i + n] = after_lines
    if not lines:
        return 1, "\n" if trailing else ""
    out = "\n".join(lines)
    return 1, out + ("\n" if trailing else "")


def _apply_hunks_to_text(
    text: str, body: list[str], path: str,
) -> tuple[str | None, str | None, int]:
    """Apply update hunks in memory. Returns (new_text, error, applied)."""
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
        hunks = [body]

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
        if not "\n".join(before_lines):
            return None, (
                f"Error: hunk #{idx + 1} in {path} has no context or removal lines"
            ), applied
        count, text = _replace_line_span(text, before_lines, after_lines)
        if count == 0:
            return None, f"Error: hunk #{idx + 1} not found in {path}", applied
        if count > 1:
            return None, (
                f"Error: hunk #{idx + 1} matches {count} locations in {path}; "
                "add more context so the match is unique"
            ), applied
        applied += 1
    return text, None, applied


def _apply_update(path: str, body: list[str]) -> str:
    if not os.path.exists(path):
        return f"Error: Update File target not found: {path}"

    # Read-before-edit gate: updating an existing file the agent never
    # read, or one changed on disk since, is refused (Claude-Code-style)
    # so a concurrent user change isn't clobbered. No-op outside a turn.
    try:
        from openprogram.store.snapshot import read_tracking as _rt
        _fresh = _rt.check_fresh(path)
        if _fresh in (_rt.NEVER_READ, _rt.STALE):
            return _rt.stale_message(path, _fresh)
    except Exception:
        pass

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    text, err, applied = _apply_hunks_to_text(text, body, path)
    if err:
        return err

    prepared = checkpoint_before_edit(path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as exc:
        if prepared:
            checkpoint_abort_edit(path, str(exc))
        raise
    checkpoint_after_edit(path, "update")
    try:
        from openprogram.store.snapshot import read_tracking as _rt
        _rt.mark_seen(path)
    except Exception:
        pass
    _emit_file_changed(path, "update")
    return f"Updated {path} ({applied} hunk{'s' if applied != 1 else ''})"


def _add_content(body: list[str]) -> str:
    content = "\n".join(l[1:] if l.startswith("+") else l for l in body)
    return content + ("" if content.endswith("\n") else "\n")


def execute(patch: str, **_: Any) -> str:
    try:
        sections = _parse_sections(patch)
    except ValueError as e:
        return f"Error parsing patch: {e}"
    if not sections:
        return "Error: patch contains no file operations"

    # Validate the complete patch before changing its first file; a later
    # denied path must not leave an earlier section partially applied.
    from openprogram.sandbox import validate_write_path
    for _op, path, _body in sections:
        if os.path.isabs(path):
            violation = validate_write_path(path)
            if violation:
                return f"Error: sandbox policy: {violation}"

    # Apply every section in memory first so a later failure does not
    # leave an earlier file already written.
    drafts: dict[str, str | None] = {}
    fresh_ok: set[str] = set()
    errors: list[str] = []

    def _draft(path: str) -> str | None:
        if path not in drafts:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    drafts[path] = f.read()
            else:
                drafts[path] = None
        return drafts[path]

    for op, path, body in sections:
        if not os.path.isabs(path):
            errors.append(f"Error: path must be absolute: {path}")
            continue
        try:
            if op == "add":
                if _draft(path) is not None:
                    errors.append(f"Error: Add File target already exists: {path}")
                else:
                    drafts[path] = _add_content(body)
                    fresh_ok.add(path)
            elif op == "update":
                text = _draft(path)
                if text is None:
                    errors.append(f"Error: Update File target not found: {path}")
                    continue
                if path not in fresh_ok:
                    try:
                        from openprogram.store.snapshot import read_tracking as _rt
                        _fresh = _rt.check_fresh(path)
                        if _fresh in (_rt.NEVER_READ, _rt.STALE):
                            errors.append(_rt.stale_message(path, _fresh))
                            continue
                    except Exception:
                        pass
                new_text, err, _applied = _apply_hunks_to_text(text, body, path)
                if err:
                    errors.append(err)
                else:
                    drafts[path] = new_text
                    fresh_ok.add(path)
            elif op == "delete":
                if _draft(path) is None:
                    errors.append(f"Error: Delete File target not found: {path}")
                else:
                    drafts[path] = None
            else:
                errors.append(f"Error: unknown op {op!r} for {path}")
        except Exception as e:
            errors.append(f"Error applying {op} to {path}: {type(e).__name__}: {e}")
    if errors:
        return "\n".join(errors)

    results: list[str] = []
    for op, path, body in sections:
        if op == "add":
            results.append(_apply_add(path, body))
        elif op == "update":
            results.append(_apply_update(path, body))
        else:
            results.append(_apply_delete(path))
    return "\n".join(results)


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    unsafe_in=['wechat', 'telegram', 'plan'],
    # Exempt: paths are parsed out of the patch text, not a path/file_path
    # argument. execute() already validate_write_path's each target.
    path_params={},
    url_params=[],
)(execute)
