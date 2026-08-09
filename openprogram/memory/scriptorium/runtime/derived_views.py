"""Deterministic views derived from authoritative Topic memory units."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..markdown import EvidenceAnnotation, MemoryUnit, is_valid_temporal_value


@dataclass(frozen=True)
class DerivedViews:
    structure_map: str
    creation_order: dict[str, int]


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
