"""Restricted changes for the memory write transaction.

The public contract is a list of whole-file ``write`` and ``delete`` changes.
Record-level CRUD/move and unified diff share the same staged Topic tree. Every
form writes only text under ``topics/**``; none can alter filesystem metadata.
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
    paragraph_spans,
    parse_topic_tree,
)
from ..markdown.syntax import (
    BLOCK_SUFFIX,
    CITATION_GROUP,
    LINK,
    SINGLE_CITATION,
    render_definition,
    source_reference,
)
from .transaction import TransactionError, validate_writable_path
from .topic_normalization import prune_empty_topic_file

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
class Destination:
    topic_path: str
    headings: tuple[str, ...] = ()
    position: str = "end"
    anchor_memory_id: str | None = None


@dataclass(frozen=True)
class MemoryChange:
    op: str
    memory_id: str | None = None
    memory_ids: tuple[str, ...] = ()
    destination: Destination | None = None
    content: str | None = None
    when: str | None = None
    source_refs: tuple[str, ...] = ()
    source_labels: tuple[str, ...] | None = None


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
    changed: set[str] = set()
    ordered = [
        change for change in parsed if change.op != "move_records"
    ] + [
        change for change in parsed if change.op == "move_records"
    ]
    for index, change in enumerate(ordered, start=1):
        if change.op == "create_record":
            assert change.destination and change.content is not None
            relative = change.destination.topic_path
            path = workspace.stage_dir / relative
            _insert_record(
                workspace,
                path,
                change,
                _fresh_evidence_label(workspace, index),
            )
            changed.add(relative)
            continue
        if change.op == "move_records":
            changed.update(_move_records(workspace, change))
            continue
        assert change.memory_id
        path, _headings, _start, _end, _ids, _citations = _locate_record(
            workspace, change.memory_id
        )
        relative = path.relative_to(workspace.stage_dir).as_posix()
        _replace_record(
            workspace,
            path,
            change,
            None
            if change.op == "delete_record"
            else _fresh_evidence_label(workspace, index),
        )
        if change.op == "delete_record":
            prune_empty_topic_file(workspace.stage_dir / relative)
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
    physical_groups = _physical_record_groups(workspace)
    planned_deleted = {
        item.get("memory_id")
        for item in changes
        if isinstance(item, dict)
        and item.get("op") in ("delete_record", "delete")
        and isinstance(item.get("memory_id"), str)
    }
    parsed: list[MemoryChange] = []
    content_targets: set[str] = set()
    moved: set[str] = set()
    updated_groups: set[frozenset[str]] = set()
    total_bytes = 0
    operations = (
        "create_record",
        "update_record",
        "delete_record",
        "move_records",
    )
    for index, item in enumerate(changes):
        prefix = f"memory_changes[{index}]"
        if not isinstance(item, dict):
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix} must be an object"
            )
        total_bytes += _string_bytes(item)
        if total_bytes > max_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT", f"memory_changes exceed {max_bytes} bytes"
            )
        legacy_op = item.get("op")
        aliases = {
            "create": "create_record",
            "update": "update_record",
            "delete": "delete_record",
        }
        if legacy_op in aliases:
            item = dict(item)
            item["op"] = aliases[legacy_op]
            if legacy_op == "create":
                item["destination"] = {
                    "topic_path": item.pop("topic_path", None),
                    "headings": item.pop("headings", []),
                    "position": "end",
                }
        op = item.get("op")
        if op not in operations:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix}.op must be " + ", ".join(operations),
            )
        allowed = {
            "create_record": {
                "op", "content", "time", "source_refs", "sources",
                "destination",
            },
            "update_record": {
                "op", "memory_id", "content", "time", "source_refs",
                "sources",
            },
            "delete_record": {"op", "memory_id"},
            "move_records": {"op", "memory_ids", "destination"},
        }[op]
        unknown = set(item) - allowed
        if unknown:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix} has unknown field: {sorted(unknown)[0]}",
            )
        if op == "move_records":
            raw_ids = item.get("memory_ids")
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in raw_ids
                )
            ):
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"{prefix}.memory_ids must be a non-empty string list",
                )
            memory_ids = tuple(value.strip() for value in raw_ids)
            if len(set(memory_ids)) != len(memory_ids):
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"{prefix}.memory_ids contains a duplicate",
                )
            selected = set(memory_ids)
            for memory_id in memory_ids:
                if memory_id not in existing:
                    raise TransactionError(
                        "MEMORY_NOT_FOUND",
                        f"memory_id does not exist: {memory_id}",
                    )
                if memory_id in moved:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        f"duplicate moved memory_id: {memory_id}",
                    )
                if memory_id in planned_deleted:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        f"deleted memory_id cannot be moved: {memory_id}",
                    )
                group = physical_groups.get(memory_id, (memory_id,))
                surviving_group = tuple(
                    value for value in group if value not in planned_deleted
                )
                if not set(surviving_group) <= selected:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        "move_records must include every ID in a shared physical block",
                    )
            groups = {
                tuple(
                    value for value in physical_groups.get(
                        memory_id, (memory_id,)
                    )
                    if value not in planned_deleted
                )
                for memory_id in memory_ids
            }
            for group in groups:
                if len(group) < 2:
                    continue
                start = min(memory_ids.index(memory_id) for memory_id in group)
                if memory_ids[start:start + len(group)] != group:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        "move_records must preserve shared physical block order",
                    )
            destination = _parse_destination(
                item.get("destination"), prefix, existing
            )
            if destination.anchor_memory_id in selected:
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    "move_records anchor_memory_id cannot be moved",
                )
            moved.update(memory_ids)
            parsed.append(MemoryChange(
                op=op,
                memory_ids=memory_ids,
                destination=destination,
            ))
            continue

        memory_id = item.get("memory_id")
        if op == "create_record":
            if "memory_id" in item:
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
            if memory_id in content_targets:
                raise TransactionError(
                    "INVALID_ARGUMENT", f"duplicate content memory_id: {memory_id}"
                )
            content_targets.add(memory_id)
            if op == "update_record":
                group = frozenset(
                    physical_groups.get(memory_id, (memory_id,))
                )
                if group in updated_groups:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        "two updates target the same merged block",
                    )
                updated_groups.add(group)
            else:
                if memory_id in moved:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        f"moved memory_id cannot be deleted: {memory_id}",
                    )
        if op == "delete_record":
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
        sources = item.get("sources")
        if source_refs is not None and sources is not None:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{prefix} cannot contain both source_refs and sources",
            )
        if sources is not None:
            if not isinstance(sources, list) or not sources:
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"{prefix}.sources must be a non-empty object list",
                )
            refs_list: list[str] = []
            labels_list: list[str] = []
            for source_index, source in enumerate(sources):
                source_prefix = f"{prefix}.sources[{source_index}]"
                if not isinstance(source, dict) or set(source) != {
                    "source", "label",
                }:
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        f"{source_prefix} must contain source and label",
                    )
                ref = source.get("source")
                label = source.get("label")
                if not isinstance(ref, str) or not ref.strip():
                    raise TransactionError(
                        "INVALID_ARGUMENT", f"{source_prefix}.source is required"
                    )
                if not isinstance(label, str) or not label.strip():
                    raise TransactionError(
                        "INVALID_ARGUMENT", f"{source_prefix}.label is required"
                    )
                ref = ref.strip()
                refs_list.append(ref)
                labels_list.append(label)
            refs = tuple(refs_list)
            labels = tuple(labels_list)
        else:
            if (
                not isinstance(source_refs, list)
                or not source_refs
                or any(
                    not isinstance(ref, str) or not ref.strip()
                    for ref in source_refs
                )
            ):
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"{prefix}.source_refs must be a non-empty string list",
                )
            refs = tuple(ref.strip() for ref in source_refs)
            labels = None
        if len(set(refs)) != len(refs):
            raise TransactionError(
                "INVALID_ARGUMENT", f"{prefix}.sources contains a duplicate source"
            )

        destination = None
        if op == "create_record":
            destination = _parse_destination(
                item.get("destination"), prefix, existing
            )
        parsed.append(MemoryChange(
            op=op,
            memory_id=memory_id,
            destination=destination,
            content=content,
            when=when,
            source_refs=refs,
            source_labels=labels,
        ))
    return parsed


def _string_bytes(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, list):
        return sum(_string_bytes(item) for item in value)
    if isinstance(value, dict):
        return sum(_string_bytes(item) for item in value.values())
    return 0


def _parse_destination(
    raw: object,
    prefix: str,
    existing_ids: set[str],
) -> Destination:
    field = f"{prefix}.destination"
    if not isinstance(raw, dict):
        raise TransactionError(
            "INVALID_ARGUMENT", f"{field} must be an object"
        )
    required = {"topic_path", "headings", "position"}
    missing = required - set(raw)
    if missing:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"{field}.{sorted(missing)[0]} is required",
        )
    unknown = set(raw) - {
        "topic_path", "headings", "position", "anchor_memory_id",
    }
    if unknown:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"{field} has unknown field: {sorted(unknown)[0]}",
        )
    topic_path = raw.get("topic_path")
    if not isinstance(topic_path, str) or not topic_path.strip():
        raise TransactionError(
            "INVALID_ARGUMENT", f"{field}.topic_path is required"
        )
    validate_writable_path(topic_path)
    topic_path = Path(topic_path).as_posix()
    raw_headings = raw.get("headings")
    if (
        not isinstance(raw_headings, list)
        or any(
            not isinstance(value, str) or not value.strip() or "\n" in value
            for value in raw_headings
        )
    ):
        raise TransactionError(
            "INVALID_ARGUMENT", f"{field}.headings must be a string list"
        )
    if len(raw_headings) > 6:
        raise TransactionError(
            "INVALID_ARGUMENT", f"{field}.headings has more than 6 levels"
        )
    position = raw.get("position")
    if position not in ("start", "end", "before", "after"):
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"{field}.position must be start, end, before or after",
        )
    anchor = raw.get("anchor_memory_id")
    if position in ("before", "after"):
        if not isinstance(anchor, str) or not anchor.strip():
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"{field}.anchor_memory_id is required for {position}",
            )
        anchor = anchor.strip()
        if anchor not in existing_ids:
            raise TransactionError(
                "MEMORY_NOT_FOUND", f"memory_id does not exist: {anchor}"
            )
    elif anchor is not None:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"{field}.anchor_memory_id is only allowed for before or after",
        )
    return Destination(
        topic_path=topic_path,
        headings=tuple(value.strip() for value in raw_headings),
        position=position,
        anchor_memory_id=anchor,
    )


def _physical_record_groups(workspace: Any) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {}
    for path in (workspace.stage_dir / "topics").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for start, end in workspace._paragraph_spans(text):
            paragraph = "\n".join(lines[start:end])
            suffix = BLOCK_SUFFIX.search(paragraph)
            if suffix is not None:
                ids = tuple(re.findall(
                    r"\^([A-Za-z0-9-]+)", paragraph[suffix.start():]
                ))
            else:
                ids = tuple(
                    value for value in SINGLE_CITATION.findall(paragraph)
                    if re.fullmatch(MEMORY_ID, value)
                )
            for memory_id in ids:
                groups[memory_id] = ids
    return groups


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
        render_definition(label, change.when, _render_sources(change)),
    ]


def _render_sources(change: MemoryChange) -> tuple[str, ...]:
    if change.source_labels is None:
        return change.source_refs
    return tuple(
        f"[{label}]({ref})"
        for ref, label in zip(change.source_refs, change.source_labels)
    )


def _insert_record(
    workspace: Any,
    path: Path,
    change: MemoryChange,
    label: str,
) -> None:
    assert change.destination is not None
    rendered = _render_record(change, label)
    _insert_blocks(
        workspace,
        path,
        change.destination,
        ["\n".join(rendered[:-2])],
        rendered[-1:],
    )


def _heading_rows(lines: list[str]) -> list[tuple[int, int, tuple[str, ...]]]:
    rows: list[tuple[int, int, tuple[str, ...]]] = []
    headings: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is None:
            continue
        level = len(match.group(1))
        headings = headings[: level - 1] + [match.group(2)]
        rows.append((index, level, tuple(headings)))
    return rows


def _ensure_heading_section(path: Path, headings: tuple[str, ...]) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        path.read_text(encoding="utf-8").splitlines()
        if path.exists()
        else []
    )
    if not headings:
        return lines
    rows = _heading_rows(lines)
    if any(row_path == headings for _index, _level, row_path in rows):
        return lines
    best: tuple[int, int, tuple[str, ...]] | None = None
    for row in rows:
        row_path = row[2]
        if row_path == headings[: len(row_path)] and (
            best is None or len(row_path) > len(best[2])
        ):
            best = row
    insert_at = len(lines)
    depth = 0
    if best is not None:
        depth = len(best[2])
        for index, level, _row_path in rows:
            if index > best[0] and level <= depth:
                insert_at = index
                break
    heading_lines = [
        f"{'#' * level} {headings[level - 1]}"
        for level in range(depth + 1, len(headings) + 1)
    ]
    before = "\n".join(lines[:insert_at]).rstrip()
    after = "\n".join(lines[insert_at:]).lstrip()
    middle = "\n\n".join(heading_lines)
    rendered = "\n\n".join(
        part for part in (before, middle, after) if part
    )
    path.write_text(rendered.rstrip() + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8").splitlines()


def _section_bounds(
    lines: list[str], headings: tuple[str, ...]
) -> tuple[int, int]:
    rows = _heading_rows(lines)
    if not headings:
        end = rows[0][0] if rows else len(lines)
        return 0, end
    for row_index, (index, _level, row_path) in enumerate(rows):
        if row_path != headings:
            continue
        end = rows[row_index + 1][0] if row_index + 1 < len(rows) else len(lines)
        return index + 1, end
    raise TransactionError(
        "INVALID_ARGUMENT", "destination heading path does not exist"
    )


def _insert_blocks(
    workspace: Any,
    path: Path,
    destination: Destination,
    paragraphs: list[str],
    definitions: list[str],
) -> None:
    lines = _ensure_heading_section(path, destination.headings)
    start, end = _section_bounds(lines, destination.headings)
    insert_at = start if destination.position == "start" else end
    if destination.position in ("before", "after"):
        assert destination.anchor_memory_id
        anchor_path, anchor_headings, anchor_start, anchor_end, _ids, _citations = (
            _locate_record(workspace, destination.anchor_memory_id)
        )
        expected_topic = Path(destination.topic_path).relative_to("topics").as_posix()
        actual_topic = anchor_path.relative_to(
            workspace.stage_dir / "topics"
        ).as_posix()
        if actual_topic != expected_topic or anchor_headings != destination.headings:
            raise TransactionError(
                "INVALID_ARGUMENT",
                "anchor_memory_id is not in the destination section",
            )
        insert_at = anchor_start if destination.position == "before" else anchor_end
    existing_definitions = {
        match.group("id"): line
        for line in lines
        if (match := definition_match(line))
    }
    inserted_definitions: list[str] = []
    for line in definitions:
        match = definition_match(line)
        if match is None:
            continue
        citation_id = match.group("id")
        existing = existing_definitions.get(citation_id)
        if existing is not None:
            if _definition_signature(
                existing,
                topic_path=path,
                topics=workspace.stage_dir / "topics",
            ) != _definition_signature(
                line,
                topic_path=path,
                topics=workspace.stage_dir / "topics",
            ):
                raise TransactionError(
                    "INVALID_ARGUMENT",
                    f"evidence definition conflicts in destination: {citation_id}",
                )
            continue
        existing_definitions[citation_id] = line
        inserted_definitions.append(line)
    definitions = inserted_definitions
    block = "\n\n".join([*paragraphs, *definitions]).strip()
    before = "\n".join(lines[:insert_at]).rstrip()
    after = "\n".join(lines[insert_at:]).lstrip()
    rendered = "\n\n".join(part for part in (before, block, after) if part)
    path.write_text(rendered.rstrip() + "\n", encoding="utf-8")


def _locate_record(
    workspace: Any,
    memory_id: str,
) -> tuple[Path, tuple[str, ...], int, int, list[str], list[str]]:
    for path in sorted((workspace.stage_dir / "topics").rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for start, end, headings in paragraph_spans(lines):
            paragraph = "\n".join(lines[start:end])
            suffix = BLOCK_SUFFIX.search(paragraph)
            if suffix is not None:
                ids = re.findall(
                    r"\^([A-Za-z0-9-]+)", paragraph[suffix.start():]
                )
            else:
                ids = [
                    value for value in SINGLE_CITATION.findall(paragraph)
                    if re.fullmatch(MEMORY_ID, value)
                ]
            if memory_id in ids:
                return (
                    path,
                    headings,
                    start,
                    end,
                    ids,
                    list(SINGLE_CITATION.findall(paragraph)),
                )
    raise TransactionError(
        "MEMORY_NOT_FOUND", f"memory_id does not exist: {memory_id}"
    )


def _definition_signature(
    line: str,
    *,
    topic_path: Path | None = None,
    topics: Path | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Compare evidence semantics independently of relative link targets."""
    match = definition_match(line)
    assert match is not None
    sources = match.group("sources")
    references = tuple(
        source_reference(
            label,
            target,
            topic_path=topic_path,
            topics=topics,
        )
        for label, target in LINK.findall(sources)
    )
    if not references:
        references = tuple(
            value.strip()
            for value in re.split(r"\s*(?:,|·)\s*", sources)
            if value.strip()
        )
    return match.group("when"), references


def _definition_for_topic(
    workspace: Any,
    line: str,
    *,
    source_path: Path,
    target_topic: Path,
) -> str:
    match = definition_match(line)
    assert match is not None
    links = LINK.findall(match.group("sources"))
    if not links:
        return line
    topics = workspace.stage_dir / "topics"
    rendered = []
    for label, target in links:
        ref = source_reference(
            label,
            target,
            topic_path=source_path,
            topics=topics,
        )
        rendered.append(workspace._source_link(target_topic, ref, label))
    return render_definition(
        match.group("id"),
        None if match.group("when") == "undated" else match.group("when"),
        rendered,
    )


def _move_records(workspace: Any, change: MemoryChange) -> set[str]:
    assert change.destination is not None
    target_topic = Path(change.destination.topic_path)
    selected = set(change.memory_ids)
    blocks: list[tuple[Path, int, int, str, tuple[str, ...]]] = []
    seen_blocks: set[tuple[Path, int, int]] = set()
    definition_lines: list[str] = []
    seen_definitions: dict[str, str] = {}
    for memory_id in change.memory_ids:
        path, _headings, start, end, ids, citations = _locate_record(
            workspace, memory_id
        )
        text = path.read_text(encoding="utf-8")
        if not set(ids) <= selected:
            raise TransactionError(
                "INVALID_ARGUMENT",
                "move_records must include every ID in a shared physical block",
            )
        key = (path, start, end)
        if key in seen_blocks:
            continue
        seen_blocks.add(key)
        lines = text.splitlines()
        blocks.append((path, start, end, "\n".join(lines[start:end]), tuple(citations)))
        definitions = {
            match.group("id"): line
            for line in lines
            if (match := definition_match(line))
        }
        for citation in citations:
            if citation not in definitions:
                continue
            definition = _definition_for_topic(
                workspace,
                definitions[citation],
                source_path=path,
                target_topic=target_topic,
            )
            existing = seen_definitions.get(citation)
            if existing is not None:
                target_path = workspace.stage_dir / target_topic
                if _definition_signature(
                    existing,
                    topic_path=target_path,
                    topics=workspace.stage_dir / "topics",
                ) != _definition_signature(
                    definition,
                    topic_path=target_path,
                    topics=workspace.stage_dir / "topics",
                ):
                    raise TransactionError(
                        "INVALID_ARGUMENT",
                        f"evidence definition conflicts across sources: {citation}",
                    )
                continue
            definition_lines.append(definition)
            seen_definitions[citation] = definition

    by_path: dict[Path, list[tuple[int, int, tuple[str, ...]]]] = {}
    for path, start, end, _paragraph, citations in blocks:
        by_path.setdefault(path, []).append((start, end, citations))
    changed: set[str] = set()
    for path, spans in by_path.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        for start, end, _citations in sorted(spans, reverse=True):
            del lines[start:end]
        moved_citations = {
            citation for _start, _end, citations in spans for citation in citations
        }
        remaining_citations = {
            citation
            for start, end, _headings in paragraph_spans(lines)
            for citation in SINGLE_CITATION.findall(
                "\n".join(lines[start:end])
            )
        }
        lines = [
            line for line in lines
            if not (
                (match := definition_match(line))
                and match.group("id") in moved_citations
                and match.group("id") not in remaining_citations
            )
        ]
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        changed.add(path.relative_to(workspace.stage_dir).as_posix())

    destination_path = workspace.stage_dir / change.destination.topic_path
    _insert_blocks(
        workspace,
        destination_path,
        change.destination,
        [paragraph for _path, _start, _end, paragraph, _citations in blocks],
        definition_lines,
    )
    for path in by_path:
        prune_empty_topic_file(path)
    changed.add(change.destination.topic_path)
    return changed


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
                    if change.op == "update_record":
                        assert change.content is not None
                        records.append(f"{change.content}[^{memory_id}]")
                else:
                    records.append(f"{content}[^{memory_id}]")
            cursor = match.end()
        if not found:
            continue
        tail = paragraph[cursor:].strip()
        if change.op == "delete_record" and not records:
            tail = ""
        replacement = " ".join(records)
        if replacement and tail:
            replacement += f" {tail}"
        lines[start:end] = replacement.splitlines() if replacement else []
        rendered_lines = []
        for line in lines:
            match = definition_match(line)
            if match is None or match.group("id") != change.memory_id:
                rendered_lines.append(line)
            elif change.op == "update_record":
                rendered_lines.append(render_definition(
                    change.memory_id,
                    change.when,
                    _render_sources(change),
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
    if change.op == "delete_record" and len(ids) > 1:
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
    if change.op == "update_record":
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
