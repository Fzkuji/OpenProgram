"""Deterministic views derived from authoritative Topic memory units."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..markdown import EvidenceAnnotation, MemoryUnit, is_valid_temporal_value


@dataclass(frozen=True)
class DerivedViews:
    structure_map: str
    creation_order: dict[str, int]


@dataclass(frozen=True)
class CoreBlock:
    """What the always-on block came to, and what did not fit in it."""

    tokens: int
    dropped: tuple[str, ...]


def _relative(target: PurePosixPath, source_dir: PurePosixPath) -> str:
    return os.path.relpath(str(target), str(source_dir)).replace(os.sep, "/")


def _source_target(unit: MemoryUnit, link: str) -> PurePosixPath:
    path, separator, fragment = link.partition("#")
    target = PurePosixPath("topics") / PurePosixPath(unit.topic_path).parent / path
    normalized = PurePosixPath(os.path.normpath(str(target)))
    return PurePosixPath(str(normalized) + (f"#{fragment}" if separator else ""))


def _structure_map(units: list[MemoryUnit]) -> str:
    rows: dict[str, list[tuple[str, ...]]] = {}
    for unit in units:
        if unit.headings not in rows.setdefault(unit.topic_path, []):
            rows[unit.topic_path].append(unit.headings)
    lines = []
    for path in sorted(rows):
        lines.append(f"topics/{path}")
        seen = set()
        for headings in rows[path]:
            for level, heading in enumerate(headings, 1):
                key = (level, heading)
                if key not in seen:
                    lines.append(f"  {'#' * level} {heading}")
                    seen.add(key)
    return "\n".join(lines)


CORE_TOPIC = "core.md"

# A Markdown link target, with its fragment left out of the match.
_LINK_TARGET = re.compile(r"(?<=\]\()(?P<path>[^)\s#]+)(?=[)#])")
_BLOCK_SUFFIX = re.compile(r"\s\^([A-Za-z0-9-]+)\s*$")
_DEFINITION = re.compile(r"^\[\^[A-Za-z0-9_-]+\]:")


def _move_links(text: str, *, source_dir: str, target_dir: str) -> str:
    """Rewrite relative link targets as a file moves between directories.

    ``topics/core.md`` cites its sources as ``../sources/…``; the same
    text rendered at the workspace root has to say ``sources/…`` or the
    footnote points nowhere.
    """
    def replace(match: re.Match[str]) -> str:
        path = match.group("path")
        if path.startswith(("http://", "https://", "mailto:", "/")):
            return path
        moved = os.path.relpath(
            os.path.normpath(os.path.join(source_dir, path)), target_dir
        )
        return moved.replace(os.sep, "/")

    return _LINK_TARGET.sub(replace, text)


def _core_chunks(text: str) -> list[tuple[str, str | None]]:
    """The file as renderable chunks, each with its block ID if it has one.

    A footnote definition belongs to the paragraph above it: dropping the
    paragraph and keeping its definition would leave the block citing
    nothing. Blank-line separation is what the Topic format already uses
    to separate paragraphs.
    """
    chunks: list[tuple[str, str | None]] = []
    for raw in re.split(r"\n\s*\n", text):
        block = raw.strip("\n")
        if not block.strip():
            continue
        if chunks and _DEFINITION.match(block.lstrip()):
            previous, block_id = chunks[-1]
            chunks[-1] = (f"{previous}\n\n{block}", block_id)
            continue
        found = _BLOCK_SUFFIX.search(block)
        chunks.append((block, found.group(1) if found else None))
    return chunks


def promote_legacy_core(memory_dir: Path) -> bool:
    """Move a hand-written ``core.md`` into ``topics/``. True if it moved.

    A workspace from before the block was derived has its always-on
    content at the root. It already carries block IDs and evidence
    footnotes, so it is a topic file as it stands — unless a hand edit
    left a paragraph the Topic format cannot parse, in which case it
    stays where it is and the render leaves it alone rather than
    replacing content nothing else holds a copy of.
    """
    from ..markdown import parse_topic_tree

    memory_dir = Path(memory_dir)
    master = memory_dir / "topics" / CORE_TOPIC
    legacy = memory_dir / CORE_TOPIC
    if master.exists() or not legacy.is_file():
        return False
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        _move_links(
            legacy.read_text(encoding="utf-8"),
            source_dir=".", target_dir="topics",
        ),
        encoding="utf-8",
    )
    try:
        parse_topic_tree(memory_dir / "topics")
    except Exception:
        master.unlink()
        return False
    return True


def _pending_core_blocks(memory_dir: Path) -> set[str]:
    """Block IDs in ``topics/core.md`` that cite un-promoted evidence.

    The always-on block is injected into every session without anyone
    asking for it, so it is the last place unvouched speech should reach.
    A block counts as pending when any Source it cites is pending or does
    not resolve — the same all-or-nothing rule retrieval applies, because
    a paragraph is one claim and cannot be half-believed.
    """
    from ..markdown import parse_topic_tree
    from ..source_format import trusted_source_ids

    try:
        units = parse_topic_tree(memory_dir / "topics")
    except Exception:
        # An unparseable topic tree is the caller's problem to report; here
        # it means no block can be shown to be trusted, so none is dropped
        # on the strength of a reading that did not happen.
        return set()
    trusted = trusted_source_ids(memory_dir)
    return {
        unit.memory_id
        for unit in units
        if unit.topic_path == CORE_TOPIC
        and not set(unit.source_refs) <= trusted
    }


def render_core_block(memory_dir: Path, *, budget_tokens: int) -> CoreBlock:
    """Rebuild ``core.md`` from ``topics/core.md`` under a token budget.

    The budget is a rendering limit, not a gate. Paragraphs go in in file
    order until the next one does not fit; what is left out stays in the
    master, still indexed and still reachable by search, so leaving a
    paragraph out costs visibility and nothing else. Anyone who wants a
    paragraph always visible moves it earlier in the master, which is an
    ordinary edit.

    A paragraph resting on pending evidence is left out for a different
    reason and does not come back by moving it: it is withheld until the
    Sources under it are promoted. Both kinds of omission report through
    ``dropped``, since from the reader's side the paragraph is simply not
    there either way.
    """
    import tiktoken

    memory_dir = Path(memory_dir)
    master = memory_dir / "topics" / CORE_TOPIC
    if not master.is_file():
        # Nothing to render from, and no licence to replace whatever the
        # workspace already has at the root.
        return CoreBlock(0, ())
    chunks = _core_chunks(
        _move_links(
            master.read_text(encoding="utf-8"),
            source_dir="topics", target_dir=".",
        )
    )
    pending = _pending_core_blocks(memory_dir)
    encoding = tiktoken.get_encoding("o200k_base")
    kept: list[str] = []
    dropped: list[str] = []
    tokens = 0
    for index, (chunk, block_id) in enumerate(chunks):
        if block_id is not None and block_id in pending:
            dropped.append(block_id)
            continue
        size = len(encoding.encode(chunk))
        if tokens + size > budget_tokens:
            dropped.extend(
                value for _chunk, value in chunks[index:] if value
            )
            break
        kept.append(chunk)
        tokens += size
    (memory_dir / CORE_TOPIC).write_text(
        "\n\n".join(kept).rstrip() + "\n", encoding="utf-8"
    )
    return CoreBlock(tokens, tuple(dropped))


def rebuild_derived_views(
    memory_dir: Path,
    units: list[MemoryUnit],
    *,
    recent_limit: int = 50,
    creation_order: dict[str, int] | None = None,
) -> DerivedViews:
    """Replace Timeline and Recent from Topic units and stable creation order."""
    if recent_limit < 0:
        raise ValueError("recent_limit must be non-negative")
    memory_dir = Path(memory_dir)
    timeline = memory_dir / "timeline"
    if timeline.exists():
        shutil.rmtree(timeline)
    timeline.mkdir(parents=True)

    order = dict(creation_order or {})
    if creation_order is None:
        order.update({unit.memory_id: unit.created_order for unit in units})
    next_order = max(order.values(), default=-1) + 1
    for unit in units:
        if unit.memory_id not in order:
            order[unit.memory_id] = next_order
            next_order += 1

    unit_ids = {unit.memory_id for unit in units}
    outbound = {
        unit.memory_id: sorted(set(unit.relation_targets)) for unit in units
    }
    backlinks: dict[str, list[str]] = {}
    for source, targets in outbound.items():
        missing = set(targets) - unit_ids
        if missing:
            raise ValueError(f"dangling block link: {sorted(missing)[0]}")
        for target in targets:
            backlinks.setdefault(target, []).append(source)
    (memory_dir / "relations.json").write_text(
        json.dumps(
            {
                "backlinks": {
                    target: sorted(sources)
                    for target, sources in sorted(backlinks.items())
                },
                "outbound": dict(sorted(outbound.items())),
            },
            ensure_ascii=False,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    grouped: dict[str, list[tuple[MemoryUnit, EvidenceAnnotation | None]]] = {}
    for unit in units:
        dated = [
            row for row in unit.evidence
            if row.when is not None and is_valid_temporal_value(row.when)
        ]
        if dated:
            for annotation in dated:
                grouped.setdefault(annotation.when, []).append((unit, annotation))
        elif unit.when is not None and is_valid_temporal_value(unit.when):
            grouped.setdefault(unit.when, []).append((unit, None))
    for when, rows in grouped.items():
        path = timeline / Path(*when.split("-"))
        path = path.with_suffix(".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        timeline_relative = PurePosixPath("timeline") / path.relative_to(timeline).as_posix()
        blocks = [f"# {when}"]
        for unit, annotation in sorted(
            rows,
            key=lambda item: (
                (item[1].source_refs if item[1] else item[0].source_refs)[0],
                item[0].memory_id,
            ),
        ):
            topic_target = PurePosixPath("topics") / unit.topic_path
            topic_link = _relative(topic_target, timeline_relative.parent)
            source_links = []
            refs = annotation.source_refs if annotation else unit.source_refs
            links = annotation.source_links if annotation else unit.source_links
            for label, link in zip(refs, links):
                target = _source_target(unit, link)
                target_path, separator, fragment = str(target).partition("#")
                relative = _relative(PurePosixPath(target_path), timeline_relative.parent)
                source_links.append(f"[{label}]({relative}{f'#{fragment}' if separator else ''})")
            fragment = (
                unit.memory_id
                if unit.memory_id.startswith("mem_")
                else f"^{unit.memory_id}"
            )
            blocks.extend([
                "",
                f"{annotation.quote if annotation else unit.content} "
                f"[Topic]({topic_link}#{fragment}) " + " ".join(source_links),
            ])
        path.write_text("\n".join(blocks).rstrip() + "\n", encoding="utf-8")

    recent_units = sorted(units, key=lambda unit: order[unit.memory_id])
    recent_units = recent_units[-recent_limit:] if recent_limit else []
    recent = memory_dir / "recent_events.jsonl"
    recent.write_text("".join(json.dumps({
        "memory_id": unit.memory_id,
        "when": unit.when,
        "whens": list(dict.fromkeys(
            annotation.when
            for annotation in unit.evidence
            if annotation.when is not None
        )),
        "content": unit.content,
        "refs": list(unit.source_refs),
        "source_refs": list(unit.source_refs),
        "topic_path": f"topics/{unit.topic_path}",
        "headings": list(unit.headings),
        "created_order": order[unit.memory_id],
    }, ensure_ascii=False) + "\n" for unit in recent_units), encoding="utf-8")
    return DerivedViews(_structure_map(units), order)
