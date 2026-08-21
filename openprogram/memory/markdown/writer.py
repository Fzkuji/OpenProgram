"""Render memory units into Topic Markdown."""

import re
from pathlib import Path

from .models import MemoryUnit
from .syntax import render_definition


def append_memory_unit(path: Path, unit: MemoryUnit) -> None:
    """Append one unit under its heading path without rewriting existing prose."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if f"[^{unit.memory_id}]" in text:
        return
    lines = text.rstrip().splitlines()
    existing_headings = {
        (len(match.group(1)), match.group(2))
        for line in lines
        if (match := re.match(r"^(#{1,6})\s+(.+?)\s*$", line))
    }
    for level, heading in enumerate(unit.headings, start=1):
        if (level, heading) not in existing_headings:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{'#' * level} {heading}")
            existing_headings.add((level, heading))
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{unit.content}[^{unit.memory_id}]")
    labels = (
        unit.source_labels
        if len(unit.source_labels) == len(unit.source_refs)
        else unit.source_refs
    )
    links = [
        f"[{label}]({target})"
        for label, target in zip(labels, unit.source_links)
    ]
    lines.extend(["", render_definition(unit.memory_id, unit.when, links)])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
