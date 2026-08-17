"""The recent view is a cross-language contract.

``rebuild_derived_views`` writes ``recent_events.jsonl``; the Memory page
reads it straight through ``GET /api/memory/recent`` and renders it. The
route copies the parsed dicts verbatim, so nothing between the two sides
would notice a rename — the page just goes blank in that column. This
test pins the field names on both ends: it runs the real rebuild and
checks the emitted keys against the ``RecentEvent`` interface parsed out
of the TypeScript source.
"""

import json
import pathlib
import re

from openprogram.memory.markdown import EvidenceAnnotation, MemoryUnit
from openprogram.memory.runtime.derived_views import rebuild_derived_views


TYPES_TS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "apps" / "web" / "components" / "memory" / "types.ts"
)


def _unit(memory_id: str, when: str | None, **kwargs) -> MemoryUnit:
    return MemoryUnit(
        memory_id=memory_id,
        content=f"content of {memory_id}",
        when=when,
        source_refs=("s1",),
        source_links=("../sources/note.md#s1",),
        topic_path="people/ada.md",
        headings=("Ada",),
        created_order=0,
        **kwargs,
    )


def _frontend_fields() -> set[str]:
    """The field names ``RecentEvent`` declares in types.ts."""
    body = re.search(
        r"export interface RecentEvent \{(.*?)\n\}", TYPES_TS.read_text("utf-8"), re.S
    )
    assert body, "RecentEvent interface not found in apps/web/components/memory/types.ts"
    fields = set(re.findall(r"^\s*(\w+)\??\s*:", body.group(1), re.M))
    assert fields, "RecentEvent declares no fields"
    return fields


def test_recent_events_keys_match_the_frontend_type(tmp_path):
    memory_dir = tmp_path / "memory"
    (memory_dir / "topics" / "people").mkdir(parents=True)
    (memory_dir / "sources").mkdir()

    rebuild_derived_views(memory_dir, [
        _unit("mem_a", "2026-01-02"),
        _unit("mem_b", None, evidence=(EvidenceAnnotation(
            citation_id="c1",
            quote="quoted from evidence",
            when="2026-01-03",
            source_refs=("s1",),
            source_links=("../sources/note.md#s1",),
        ),)),
    ])

    rows = [
        json.loads(line)
        for line in (memory_dir / "recent_events.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert [row["memory_id"] for row in rows] == ["mem_a", "mem_b"]
    # The page shows `topic_path` next to `when`, so it must be the
    # rendered path, not the bare topic file name.
    assert rows[0]["topic_path"] == "topics/people/ada.md"
    assert rows[1]["when"] is None

    missing = _frontend_fields() - set(rows[0])
    assert not missing, (
        f"RecentEvent expects {sorted(missing)}, which rebuild_derived_views "
        f"does not write. Emitted keys: {sorted(rows[0])}"
    )
