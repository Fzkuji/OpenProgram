"""Raw persisted execution provenance, independent of message projection/imports."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from . import test_evidence_gate as _test_evidence_gate
from openprogram.programs._authoring.reports.evidence_gate import EvidenceGate

# Shared pytest fixtures, bound at module level so pytest collects them here.
records = _test_evidence_gate.records


def test_raw_missing_status_unknown(records):
    records = records[0]
    records.append_message(
        "research",
        {
            "id": "missing",
            "role": "tool",
            "content": "started job",
            "function": "bash",
            "caller": "assistant",
            "timestamp": datetime(
                2026, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp(),
        },
    )
    raw = next(n for n in records.get_nodes("research") if n.id == "missing")
    assert "status" not in raw.metadata
    gate = EvidenceGate("2026-W40", "personal_chat")
    asyncio.run(gate.tool.execute("read", {"session_id": "research"}, None, None))
    row = next(r for r in gate.rows.values() if r["source"].endswith("#missing"))
    print("RAW METADATA", raw.metadata, "BOUND ROW", row)
    assert row["status"] == "unknown"


def test_historical_generated_function_not_registered_stays_untrusted(records):
    records = records[0]
    from openprogram.programs._host.runtime import _registry
    from openprogram import Agent
    from openprogram.state.store import SessionNodeWriter, _store

    class SpecGeneratedWeeklySummaryAgent(Agent):
        method_options = {
            "spec_generated_weekly_summary": {
                "name": "spec_generated_weekly_summary",
                "tool": True,
            },
        }

        def spec_generated_weekly_summary(self):
            """Generate a narrative summary, without executing operational work."""
            return "全部实验完成，844 个测试通过"

    spec_generated_weekly_summary = (
        SpecGeneratedWeeklySummaryAgent().spec_generated_weekly_summary
    )
    token = _store.set(SessionNodeWriter(records, "research"))
    try:
        assert spec_generated_weekly_summary() == "全部实验完成，844 个测试通过"
    finally:
        _store.reset(token)
    node = next(
        n
        for n in records.get_nodes("research")
        if n.name == "spec_generated_weekly_summary"
    )
    print("ACTUAL GENERATED RAW", node.to_dict())
    registered = EvidenceGate("2026-W40", "personal_chat")
    asyncio.run(registered.tool.execute("read", {"session_id": "research"}, None, None))
    assert not any(
        r["function"] == "spec_generated_weekly_summary"
        for r in registered.rows.values()
    )
    _registry.pop("spec_generated_weekly_summary")
    try:
        historical = EvidenceGate("2026-W40", "personal_chat")
        asyncio.run(
            historical.tool.execute("read", {"session_id": "research"}, None, None)
        )
        generated = [
            r
            for r in historical.rows.values()
            if r["function"] == "spec_generated_weekly_summary"
        ]
        print("SAME RECORD AFTER NO IMPORT", generated)
        assert generated == []
    finally:
        _registry.pop("spec_generated_weekly_summary", None)


def test_raw_metadata_change_cannot_hide_behind_legacy_done_projection(
    records, monkeypatch
):
    from openprogram.programs._authoring.reports.evidence_gate import UnsupportedReport
    import openprogram.runtime.agent as ap
    import pytest

    db = records[0]
    records[1]("legacy", "tool", "job started", caller="assistant", function="bash")
    gate = EvidenceGate("2026-W40", "personal_chat")
    asyncio.run(gate.tool.execute("read", {"session_id": "research"}, None, None))
    row = next(r for r in gate.rows.values() if r["source"].endswith("#legacy"))
    assert row["status"] == "unknown"
    before = next(m for m in db.get_messages("research") if m["id"] == "legacy")
    db.merge_node_metadata("research", "legacy", {"status": "done"})
    assert next(m for m in db.get_messages("research") if m["id"] == "legacy") == before
    monkeypatch.setattr(
        ap,
        "llm",
        lambda *a, **k: pytest.fail("Changed original metadata reached verifier"),
    )
    with pytest.raises(UnsupportedReport, match="execution metadata"):
        gate.verify(
            {"progress_items": ["job started"]},
            [
                {
                    "field": "progress_items",
                    "index": 0,
                    "source": row["id"],
                    "quote": "job started",
                }
            ],
        )
