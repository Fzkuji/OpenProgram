"""Restricted changes for the memory write transaction.

The public contract is a list of whole-file ``write`` and ``delete`` changes.
Record-level create/update/delete and unified diff share the same staged Topic
tree. Every form writes only text under ``topics/**``; none can rename files or
alter filesystem metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..markdown import (
    MEMORY_ID,
    definition_match,
    is_valid_temporal_value,
    parse_topic_tree,
)
from ..markdown.syntax import (
    BLOCK_SUFFIX,
    CITATION_GROUP,
    SINGLE_CITATION,
    render_definition,
)
from .transaction import TransactionError, validate_writable_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_REJECTED_HEADERS = (
    ("rename from", "patch renames are not supported"),
    ("rename to", "patch renames are not supported"),
    ("copy from", "patch copies are not supported"),
    ("copy to", "patch copies are not supported"),
    ("old mode", "patch mode changes are not supported"),
    ("new mode", "patch mode changes are not supported"),
    ("new file mode 120000", "symlink creation is not supported"),
    ("deleted file mode 120000", "symlink deletion is not supported"),
    ("GIT binary patch", "binary patches are not supported"),
    ("Binary files", "binary patches are not supported"),
)


@dataclass
class FilePatch:
    path: str
    hunks: list[tuple[int, list[str]]]
    creates: bool = False
    deletes: bool = False


@dataclass(frozen=True)
class FileChange:
    path: str
    action: str
    content: str | None = None


@dataclass(frozen=True)
class MemoryChange:
    op: str
    memory_id: str | None = None
    topic_path: str | None = None
    headings: tuple[str, ...] = ()
    content: str | None = None
    when: str | None = None
    source_refs: tuple[str, ...] = ()


def apply_memory_changes(
    workspace: Any,
    changes: object,
    *,
    max_changes: int = 64,
    max_bytes: int = 512_000,
) -> list[str]:
    """Render validated record operations into staged Topic Markdown."""
    parsed = _parse_memory_changes(
        workspace, changes, max_changes=max_changes, max_bytes=max_bytes
    )
    units = {
        unit.memory_id: unit
        for unit in parse_topic_tree(workspace.stage_dir / "topics")
    }
    changed: set[str] = set()
    for index, change in enumerate(parsed, start=1):
        if change.op == "create":
            assert change.topic_path and change.content is not None
            path = workspace.stage_dir / change.topic_path
            _append_record(path, change, _fresh_evidence_label(workspace, index))
            changed.add(change.topic_path)
            continue
        assert change.memory_id
        unit = units.get(change.memory_id)
        if unit is None:
            raise TransactionError(
                "MEMORY_NOT_FOUND",
                f"memory_id does not exist: {change.memory_id}",
            )
        relative = (Path("topics") / unit.topic_path).as_posix()
        _replace_record(
            workspace,
            workspace.stage_dir / relative,
            change,
            None
            if change.op == "delete"
            else _fresh_evidence_label(workspace, index),
        )
        changed.add(relative)
    return sorted(changed)


def _parse_memory_changes(
    workspace: Any,
    changes: object,
    *,
    max_changes: int,
    max_bytes: int,
) -> list[MemoryChange]:
    if not isinstance(changes, list):
        raise TransactionError(
            "INVALID_ARGUMENT", "memory_changes must be a list"
        )
    if not changes:
        raise TransactionError("INVALID_ARGUMENT", "memory_changes is empty")
    if len(changes) > max_changes:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"at most {max_changes} memory changes per transaction",
        )
    existing = {
        unit.memory_id
        for unit in parse_topic_tree(workspace.stage_dir / "topics")
    }
    alias_groups: dict[str, frozenset[str]] = {}
    for path in (workspace.stage_dir / "topics").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for start, end in workspace._paragraph_spans(text):
            paragraph = "\n".join(lines[start:end])
            suffix = BLOCK_SUFFIX.search(paragraph)
            if suffix is None:
                continue
            ids = frozenset(re.findall(
                r"\^([A-Za-z0-9-]+)", paragraph[suffix.start():]
            ))
            for memory_id in ids:
                alias_groups[memory_id] = ids
    parsed: list[MemoryChange] = []
    targeted: set[str] = set()
    updated_groups: set[frozenset[str]] = set()
    total_bytes = 0
    allowed = {
        "op", "memory_id", "topic_path", "headings",
        "content", "time", "source_refs",
    }
    for index, item in enumerate(changes):
        prefix = f"memory_changes[{index}]"
        if not isinstance(item, dict):
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix} must be an object"
            )
        for value in item.values():
            if isinstance(value, str):
                total_bytes += len(value.encode("utf-8"))
            elif isinstance(value, list):
                total_bytes += sum(
                    len(element.encode("utf-8"))
                    for element in value
                    if isinstance(element, str)
                )
        if total_bytes > max_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT", f"memory_changes exceed {max_bytes} bytes"
            )
        unknown = set(item) - allowed
        if unknown:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix} has unknown field: {sorted(unknown)[0]}",
            )
        op = item.get("op")
        if op not in ("create", "update", "delete"):
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix}.op must be create, update or delete"
            )
        memory_id = item.get("memory_id")
        if op == "create":
            if memory_id is not None:
                raise TransactionError(
                    "INVALID_ARGUMENT", f"{prefix}.memory_id is Runtime-assigned"
                )
        else:
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise TransactionError(
                    "INVALID_ARGUMENT", f"{prefix}.memory_id is required"
                )
            if memory_id not in existing:
                raise TransactionError(
                    "MEMORY_NOT_FOUND", f"memory_id does not exist: {memory_id}"
                )
            if memory_id in targeted:
                raise TransactionError(
                    "INVALID_ARGUMENT", f"duplicate memory_id: {memory_id}"
                )
            targeted.add(memory_id)
            if op == "update":
                group = alias_groups.get(memory_id, frozenset({memory_id}))
                if group in updated_groups:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        "two updates target the same merged block",
                    )
                updated_groups.add(group)
        if op == "delete":
            extras = set(item) - {"op", "memory_id"}
            if extras:
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"{prefix}.{sorted(extras)[0]} is not allowed for delete",
                )
            parsed.append(MemoryChange(op=op, memory_id=memory_id))
            continue

        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix}.content must be a non-empty string"
            )
        content = content.strip()
        if (
            "\n\n" in content
            or any(line.lstrip().startswith("#") for line in content.splitlines())
            or SINGLE_CITATION.search(content)
            or re.search(r"(?m)\s\^[A-Za-z0-9-]+\s*$", content)
        ):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix}.content must be one final paragraph without Runtime IDs",
            )
        when = item.get("time")
        if when == "undated":
            when = None
        if "time" not in item or (
            when is not None
            and (not isinstance(when, str) or not is_valid_temporal_value(when))
        ):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix}.time must be null or YYYY, YYYY-MM, or YYYY-MM-DD",
            )
        source_refs = item.get("source_refs")
        if (
            not isinstance(source_refs, list)
            or not source_refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in source_refs)
        ):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix}.source_refs must be a non-empty string list",
            )
        refs = tuple(ref.strip() for ref in source_refs)
        if len(set(refs)) != len(refs):
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix}.source_refs contains a duplicate"
            )

        topic_path = item.get("topic_path")
        headings: tuple[str, ...] = ()
        if op == "create":
            if not isinstance(topic_path, str) or not topic_path.strip():
                raise TransactionError(
                    "INVALID_ARGUMENT", f"{prefix}.topic_path is required"
                )
            validate_writable_path(topic_path)
            topic_path = Path(topic_path).as_posix()
            raw_headings = item.get("headings", [])
            if (
                not isinstance(raw_headings, list)
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or "\n" in value
                    for value in raw_headings
                )
            ):
                raise TransactionError(
                    "INVALID_ARGUMENT", f"{prefix}.headings must be a string list"
                )
            if len(raw_headings) > 6:
                raise TransactionError(
                    "INVALID_ARGUMENT", f"{prefix}.headings has more than 6 levels"
                )
            headings = tuple(value.strip() for value in raw_headings)
        elif "topic_path" in item or "headings" in item:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix}.topic_path and headings are only allowed for create",
            )

        parsed.append(MemoryChange(
            op=op,
            memory_id=memory_id,
            topic_path=topic_path,
            headings=headings,
            content=content,
            when=when,
            source_refs=refs,
        ))
    return parsed


def _fresh_evidence_label(workspace: Any, index: int) -> str:
    used = {
        match.group("id")
        for path in (workspace.stage_dir / "topics").rglob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := definition_match(line))
    }
    counter = index
    while f"new-evidence-record-{counter}" in used:
        counter += 1
    return f"new-evidence-record-{counter}"


def _render_record(change: MemoryChange, label: str, suffix: str = "") -> list[str]:
    assert change.content is not None
    lines = change.content.splitlines()
    lines[-1] = lines[-1].rstrip() + f"[^{label}]" + suffix
    return [
        *lines,
        "",
        render_definition(label, change.when, change.source_refs),
    ]


def _append_record(path: Path, change: MemoryChange, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.rstrip().splitlines()
    for level, heading in enumerate(change.headings, start=1):
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{'#' * level} {heading}")
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(_render_record(change, label))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _record_span(workspace: Any, text: str, memory_id: str):
    lines = text.splitlines()
    for start, end in workspace._paragraph_spans(text):
        paragraph = "\n".join(lines[start:end])
        suffix = BLOCK_SUFFIX.search(paragraph)
        if suffix is None:
            continue
        ids = re.findall(r"\^([A-Za-z0-9-]+)", paragraph[suffix.start():])
        if memory_id in ids:
            return start, end, ids, SINGLE_CITATION.findall(paragraph)
    raise TransactionError(
        "MEMORY_NOT_FOUND", f"memory_id does not exist: {memory_id}"
    )


def _replace_legacy_record(
    workspace: Any,
    path: Path,
    change: MemoryChange,
    _label: str | None,
) -> bool:
    """Edit one legacy unit while retaining peer units and unbound prose."""
    assert change.memory_id
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for start, end in workspace._paragraph_spans(text):
        paragraph = "\n".join(lines[start:end])
        if BLOCK_SUFFIX.search(paragraph):
            continue
        records: list[str] = []
        cursor = 0
        found = False
        for match in CITATION_GROUP.finditer(paragraph):
            ids = tuple(SINGLE_CITATION.findall(match.group(0)))
            if not ids or any(not re.fullmatch(MEMORY_ID, value) for value in ids):
                continue
            content = paragraph[cursor:match.start()].strip()
            if not content:
                raise TransactionError(
                    "INVALID_TOPIC_FORMAT",
                    f"missing memory content before {ids[0]}",
                )
            for memory_id in ids:
                if memory_id == change.memory_id:
                    found = True
                    if change.op == "update":
                        assert change.content is not None
                        records.append(f"{change.content}[^{memory_id}]")
                else:
                    records.append(f"{content}[^{memory_id}]")
            cursor = match.end()
        if not found:
            continue
        tail = paragraph[cursor:].strip()
        if change.op == "delete" and not records and tail:
            raise TransactionError(
                "INVALID_ARGUMENT",
                "deleting this legacy memory would remove unbound prose; "
                "use direct file editing",
            )
        replacement = " ".join(records)
        if replacement and tail:
            replacement += f" {tail}"
        lines[start:end] = replacement.splitlines() if replacement else []
        rendered_lines = []
        for line in lines:
            match = definition_match(line)
            if match is None or match.group("id") != change.memory_id:
                rendered_lines.append(line)
            elif change.op == "update":
                rendered_lines.append(render_definition(
                    change.memory_id, change.when, change.source_refs
                ))
        lines = rendered_lines
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return True
    return False


def _replace_record(
    workspace: Any,
    path: Path,
    change: MemoryChange,
    label: str | None,
) -> None:
    assert change.memory_id
    text = path.read_text(encoding="utf-8")
    try:
        start, end, ids, citations = _record_span(
            workspace, text, change.memory_id
        )
    except TransactionError as exc:
        if exc.code != "MEMORY_NOT_FOUND" or not _replace_legacy_record(
            workspace, path, change, label
        ):
            raise
        return
    lines = text.splitlines()
    if change.op == "delete" and len(ids) > 1:
        paragraph = "\n".join(lines[start:end])
        suffix = BLOCK_SUFFIX.search(paragraph)
        assert suffix is not None
        kept = [value for value in ids if value != change.memory_id]
        paragraph = paragraph[:suffix.start()].rstrip() + "".join(
            f" ^{value}" for value in kept
        )
        lines[start:end] = paragraph.splitlines()
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return
    other_citations: set[str] = set()
    for other_start, other_end in workspace._paragraph_spans(text):
        if (other_start, other_end) == (start, end):
            continue
        other_citations.update(
            SINGLE_CITATION.findall("\n".join(lines[other_start:other_end]))
        )
    removable = set(citations) - other_citations
    lines = [
        line
        for line in lines
        if not (
            (match := definition_match(line))
            and match.group("id") in removable
        )
    ]
    text = "\n".join(lines)
    start, end, _ids, _citations = _record_span(
        workspace, text, change.memory_id
    )
    replacement = []
    if change.op == "update":
        assert label is not None
        replacement = _render_record(
            change, label, "".join(f" ^{value}" for value in ids)
        )
    lines = text.splitlines()
    lines[start:end] = replacement
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def apply_changes(
    stage_dir: Path,
    changes: object,
    *,
    max_changes: int = 64,
    max_bytes: int = 512_000,
) -> list[str]:
    """Apply validated whole-file changes inside ``stage_dir``."""
    if not isinstance(changes, list):
        raise TransactionError("INVALID_ARGUMENT", "changes must be a list")
    if not changes:
        raise TransactionError("INVALID_ARGUMENT", "changes is empty")
    if len(changes) > max_changes:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"at most {max_changes} changes per transaction",
        )

    parsed: list[FileChange] = []
    seen: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            raise TransactionError(
                "INVALID_ARGUMENT", f"changes[{index}] must be an object"
            )
        unknown = set(item) - {"path", "action", "content"}
        if unknown:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"changes[{index}] has unknown field: {sorted(unknown)[0]}",
            )
        path = item.get("path")
        action = item.get("action")
        if not isinstance(path, str) or not path.strip():
            raise TransactionError(
                "INVALID_ARGUMENT", f"changes[{index}].path must be a string"
            )
        validate_writable_path(path)
        normalized_path = Path(path).as_posix()
        if normalized_path in seen:
            raise TransactionError(
                "INVALID_ARGUMENT", f"duplicate change path: {normalized_path}"
            )
        seen.add(normalized_path)
        if action not in ("write", "delete"):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"changes[{index}].action must be write or delete",
            )
        content = item.get("content")
        if action == "write" and not isinstance(content, str):
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"changes[{index}].content must be a string for write",
            )
        if action == "delete" and "content" in item:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"changes[{index}].content is not allowed for delete",
            )
        total_bytes += len(normalized_path.encode("utf-8"))
        if content is not None:
            total_bytes += len(content.encode("utf-8"))
        if total_bytes > max_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT", f"changes exceed {max_bytes} bytes"
            )
        target = stage_dir / normalized_path
        if action == "delete" and not target.is_file():
            raise TransactionError(
                "PATCH_CONFLICT",
                "change deletes a file that does not exist",
                path=normalized_path,
            )
        parsed.append(FileChange(normalized_path, action, content))

    for change in parsed:
        target = stage_dir / change.path
        if change.action == "delete":
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content or "", encoding="utf-8")
    return sorted(change.path for change in parsed)


def apply_patch(stage_dir: Path, patch: str) -> list[str]:
    """Apply ``patch`` inside ``stage_dir`` and return changed paths."""
    if not patch.strip():
        raise TransactionError("INVALID_ARGUMENT", "patch is empty")
    files = _parse(patch)
    if not files:
        raise TransactionError(
            "INVALID_ARGUMENT", "patch contains no file sections"
        )
    changed: list[str] = []
    for entry in files:
        validate_writable_path(entry.path)
        target = stage_dir / entry.path
        if entry.deletes:
            if not target.is_file():
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch deletes a file that does not exist",
                    path=entry.path,
                )
            target.unlink()
            changed.append(entry.path)
            continue
        original = (
            target.read_text(encoding="utf-8").splitlines(keepends=True)
            if target.is_file()
            else []
        )
        if entry.creates and original:
            raise TransactionError(
                "PATCH_CONFLICT",
                "patch creates a file that already exists",
                path=entry.path,
            )
        if not entry.creates and not original:
            raise TransactionError(
                "PATCH_CONFLICT",
                "patch modifies a file that does not exist",
                path=entry.path,
            )
        updated = _apply_hunks(entry, original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(updated), encoding="utf-8")
        changed.append(entry.path)
    return sorted(set(changed))


def _parse(patch: str) -> list[FilePatch]:
    lines = patch.splitlines()
    files: list[FilePatch] = []
    current: FilePatch | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        for marker, message in _REJECTED_HEADERS:
            if line.startswith(marker):
                raise TransactionError("INVALID_ARGUMENT", message)
        if line.startswith("--- "):
            old = _strip_prefix(line[4:])
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise TransactionError(
                    "INVALID_ARGUMENT", "malformed patch: --- without +++"
                )
            new = _strip_prefix(lines[index + 1][4:])
            path = new if new != "/dev/null" else old
            if path == "/dev/null":
                raise TransactionError(
                    "INVALID_ARGUMENT", "patch section has no file path"
                )
            current = FilePatch(
                path=path,
                hunks=[],
                creates=old == "/dev/null",
                deletes=new == "/dev/null",
            )
            files.append(current)
            index += 2
            continue
        match = _HUNK.match(line)
        if match:
            if current is None:
                raise TransactionError(
                    "INVALID_ARGUMENT", "patch hunk outside a file section"
                )
            start = int(match.group(1))
            body: list[str] = []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if candidate.startswith(("--- ", "diff --git")) or _HUNK.match(
                    candidate
                ):
                    break
                if candidate.startswith(("+", "-", " ", "\\")):
                    body.append(candidate)
                    index += 1
                    continue
                if not candidate:
                    # A context line whose content is empty loses its leading
                    # space in many editors; treat it as blank context.
                    body.append(" ")
                    index += 1
                    continue
                break
            current.hunks.append((start, body))
            continue
        index += 1
    return files


def _strip_prefix(value: str) -> str:
    path = value.split("\t")[0].strip()
    if path in ("/dev/null", ""):
        return "/dev/null"
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _apply_hunks(entry: FilePatch, original: list[str]) -> list[str]:
    if not entry.hunks:
        raise TransactionError(
            "INVALID_ARGUMENT",
            "patch file section has no hunks",
            path=entry.path,
        )
    result: list[str] = []
    cursor = 0
    for start, body in sorted(entry.hunks, key=lambda item: item[0]):
        begin = max(start - 1, 0)
        if begin < cursor:
            raise TransactionError(
                "PATCH_CONFLICT",
                "overlapping patch hunks",
                path=entry.path,
            )
        result.extend(original[cursor:begin])
        cursor = begin
        for raw in body:
            if raw.startswith("\\"):
                continue
            marker, text = raw[0], raw[1:]
            if marker == "+":
                result.append(text + "\n")
                continue
            if cursor >= len(original):
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch context runs past end of file",
                    path=entry.path,
                    details={"line": cursor + 1},
                )
            existing = original[cursor]
            if existing.rstrip("\n") != text.rstrip("\n"):
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch context does not match file contents",
                    path=entry.path,
                    details={
                        "line": cursor + 1,
                        "expected": text,
                        "found": existing.rstrip("\n"),
                    },
                )
            if marker == " ":
                result.append(existing)
            cursor += 1
    result.extend(original[cursor:])
    return result
