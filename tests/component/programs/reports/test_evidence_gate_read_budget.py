"""Fresh public probes for identical native read argument semantics."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from openprogram.programs._authoring.reports.evidence_gate import EvidenceGate


@pytest.mark.parametrize("max_chars", [1000, 0])
def test_native_visible_owner_stays_bound_at_minimum_transcript_budget(tmp_path, monkeypatch, max_chars):
    from openprogram.state.store import SessionStore
    from openprogram.runtime.context import session_db
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.models.providers.types import Tool, ToolCall
    from openprogram.models.providers.utils.validation import validate_tool_call
    db = SessionStore(tmp_path / "sessions")
    db.create_session("research", agent_id="main")
    db.append_message("research", {"id":"owner", "role":"user", "content":"本周完成实验；下周继续验证。",
        "timestamp": datetime(2026, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp(),
        **owner_authority("owner/install/" + "a" * 16)})
    db.append_message("research", {"id":"assistant", "role":"assistant", "content":"x" * 3000,
        "predecessor":"owner"})
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    gate = EvidenceGate("2026-W40", "personal_chat")
    args = {"session_id":"research", "max_chars": max_chars}
    descriptor = Tool(name=gate.tool.name, description=gate.tool.description, parameters=gate.tool.parameters)
    args = validate_tool_call([descriptor], ToolCall(id="read", name=gate.tool.name, arguments=args))
    receipt = asyncio.run(gate.tool.execute("read", args, None, None))
    assert not receipt.is_error
    assert "本周完成实验" in receipt.content[0].text
    assert "later turn" in receipt.content[0].text.lower() or "truncated" in receipt.content[0].text.lower()
    assert {row["source"] for row in gate.rows.values()} == {"conversation:research#owner"}
    db._save_index()


def test_a_null_budget_reads_like_the_native_default(tmp_path, monkeypatch):
    from openprogram.state.store import SessionStore
    from openprogram.runtime.context import session_db
    db = SessionStore(tmp_path / "sessions")
    db.create_session("research", agent_id="main")
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    gate = EvidenceGate("2026-W40", "personal_chat")
    args = {"session_id":"research", "max_chars":None}
    original = asyncio.run(gate.native.execute("native", args, None, None))
    receipt = asyncio.run(gate.tool.execute("read", args, None, None))
    assert receipt.content[0] == original.content[0]
    assert receipt.is_error is original.is_error is False
    assert "[read_conversation error]" not in receipt.content[0].text
    assert gate.rows == {}
    db._save_index()
