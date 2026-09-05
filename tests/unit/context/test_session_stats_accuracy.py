from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.context import session_stats as stats
from openprogram.context.system_prompt_node import latest_recorded_prompt
from openprogram.context.tool_snapshot_node import latest_recorded_tool_snapshot


def test_build_stats_reuses_the_supplied_breakdown_estimate(monkeypatch):
    monkeypatch.setattr(
        stats,
        "estimate_total_used",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the route already computed this breakdown")
        ),
    )

    result = stats.build_stats(
        "session",
        measured_total=900,
        window=2_000,
        estimated_total=600,
    )

    assert result == {
        "window": 2_000,
        "total_used": 900,
        "basis": "measured",
        "estimated": 600,
        "calibration": 1.5,
    }


def test_finalize_breakdown_exposes_unclassified_provider_residual():
    breakdown = {
        "context_window": 2_000,
        "system_prompt": 100,
        "messages": 200,
        "tools_schema": 100,
        "tools_deferred_catalog": 0,
        "mcp_tools": 0,
        "mcp_tools_deferred": 0,
        "memory": 0,
        "skills": 0,
        "input_used": 400,
    }

    result = stats.finalize_breakdown(
        breakdown,
        {
            "window": 2_000,
            "total_used": 1_000,
            "basis": "measured",
            "estimated": 400,
        },
    )

    assert result["classified_estimate"] == 400
    assert result["classified_used"] == 400
    assert result["unclassified"] == 600
    assert result["free_space"] == 1_000
    assert sum(result[key] for key in stats.BREAKDOWN_CATEGORY_KEYS) == 1_000


def test_finalize_breakdown_calibrates_categories_when_estimate_is_high():
    breakdown = {
        "context_window": 2_000,
        "system_prompt": 300,
        "messages": 300,
        "tools_schema": 0,
        "tools_deferred_catalog": 0,
        "mcp_tools": 0,
        "mcp_tools_deferred": 0,
        "memory": 0,
        "skills": 0,
        "input_used": 600,
    }

    result = stats.finalize_breakdown(
        breakdown,
        {
            "window": 2_000,
            "total_used": 300,
            "basis": "measured",
            "estimated": 600,
        },
    )

    assert result["classification_scale"] == pytest.approx(0.5)
    assert result["system_prompt"] == 150
    assert result["messages"] == 150
    assert result["unclassified"] == 0
    assert sum(result[key] for key in stats.BREAKDOWN_CATEGORY_KEYS) == 300


def test_compute_breakdown_counts_memory_and_structured_tool_payloads(monkeypatch):
    captured: dict = {}

    class FakeDB:
        def get_branch(self, _session_id, _head_id=None):
            return [
                {
                    "id": "u1",
                    "role": "user",
                    "content": "question",
                    "memory_prefetch": "<memory-context>recalled fact</memory-context>",
                },
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "answer",
                    "extra": json.dumps({
                        "blocks": [
                            {"type": "thinking", "text": "private reasoning"},
                            {
                                "type": "tool",
                                "tool": "read",
                                "input": {"path": "/tmp/example"},
                                "result": "tool result text",
                                "is_error": False,
                            },
                        ],
                    }),
                },
            ]

        def get_messages(self, _session_id):
            return self.get_branch(_session_id)

        def get_session(self, _session_id):
            return {"model": "", "tools_enabled": False}

    def fake_call_breakdown(**kwargs):
        captured.update(kwargs)
        return {
            "messages": 10,
            "system_prompt": 0,
            "skills": 0,
            "memory": 0,
            "tools_schema": 0,
            "tools_deferred_catalog": 0,
            "mcp_tools": 0,
            "input_used": 10,
            "tools": [],
        }

    import openprogram.agent.session_db as session_db
    import openprogram.context.breakdown as breakdown
    import openprogram.skills.loader as skill_loader
    import openprogram.memory as memory
    import openprogram.programs._runtime as runtime

    monkeypatch.setattr(session_db, "default_db", lambda: FakeDB())
    monkeypatch.setattr(breakdown, "compute_call_breakdown", fake_call_breakdown)
    monkeypatch.setattr(skill_loader, "list_skills", lambda: [])
    monkeypatch.setattr(memory, "get_backend", lambda: type("B", (), {"system_prompt": lambda self: ""})())
    monkeypatch.setattr(runtime, "all_tools", lambda: [])

    stats.compute_breakdown("session")

    user_content = captured["history"][0]["content"]
    assistant_content = captured["history"][1]["content"]
    assert "recalled fact" in user_content
    assert "private reasoning" in assistant_content
    assert "/tmp/example" in assistant_content
    assert "tool result text" in assistant_content


def test_compute_breakdown_uses_recorded_prompt_and_tool_snapshot(monkeypatch):
    captured: dict = {}

    class FakeDB:
        def get_branch(self, _session_id, _head_id=None):
            return [
                {"id": "u1", "role": "user", "content": "question"},
                {"id": "a1", "role": "assistant", "content": "answer"},
            ]

        def get_messages(self, _session_id):
            return self.get_branch(_session_id)

        def get_nodes(self, _session_id):
            return [
                {
                    "id": "u1", "seq": 1, "role": "user",
                    "output": "question",
                },
                {
                    "id": "sp", "seq": 2, "role": "code",
                    "name": "context/system_prompt",
                    "output": "RECORDED PROMPT",
                    "metadata": {"anchor_head_id": "u1"},
                },
                {
                    "id": "ts", "seq": 3, "role": "code",
                    "name": "context/tool_snapshot",
                    "output": json.dumps({
                        "tools": [
                            {
                                "name": "recorded_tool",
                                "tokens": 123,
                                "deferred": False,
                                "server": "",
                            },
                            {
                                "name": "recorded_mcp",
                                "tokens": 17,
                                "deferred": True,
                                "server": "docs",
                            },
                        ],
                        }),
                    "metadata": {"anchor_head_id": "u1"},
                },
                {
                    "id": "a1", "seq": 4, "role": "llm",
                    "output": "answer",
                },
            ]

        def get_session(self, _session_id):
            return {"head_id": "a1", "model": ""}

    def fake_call_breakdown(**kwargs):
        captured.update(kwargs)
        return {
            "messages": 20,
            "system_prompt": 40,
            "skills": 0,
            "memory": 0,
            "tools_schema": 0,
            "tools_deferred_catalog": 0,
            "mcp_tools": 0,
            "input_used": 60,
            "tools": [],
        }

    import openprogram.agent.session_db as session_db
    import openprogram.context.breakdown as breakdown
    import openprogram.programs as programs
    import openprogram.skills.loader as skill_loader
    import openprogram.memory as memory

    monkeypatch.setattr(session_db, "default_db", lambda: FakeDB())
    monkeypatch.setattr(breakdown, "compute_call_breakdown", fake_call_breakdown)
    monkeypatch.setattr(
        programs,
        "agent_tools",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("recorded snapshots must not read the live registry")
        ),
    )
    monkeypatch.setattr(skill_loader, "list_skills", lambda: [])
    monkeypatch.setattr(memory, "get_backend", lambda: type("B", (), {"system_prompt": lambda self: ""})())

    result = stats.compute_breakdown("session", head_id="a1")

    assert captured["system_prompt"] == "RECORDED PROMPT"
    assert captured["tools"] == []
    assert result["tools_source"] == "recorded_snapshot"
    assert result["tools_schema"] == 123
    assert result["mcp_tools_deferred"] == 17
    assert result["tools"] == [
        {"name": "recorded_tool", "tokens": 123, "deferred": False},
        {"name": "recorded_mcp", "tokens": 17, "deferred": True},
    ]


def test_recorded_inputs_follow_the_selected_branch_ancestry():
    class FakeStore:
        def get_nodes(self, _session_id):
            return [
                {"id": "root", "seq": 1, "role": "user", "output": "root"},
                {"id": "a-user", "seq": 2, "role": "user", "predecessor": "root"},
                {"id": "sp-a", "seq": 3, "name": "context/system_prompt", "output": "A",
                 "metadata": {}},
                {
                    "id": "ts-a", "seq": 4, "name": "context/tool_snapshot",
                    "output": json.dumps({"tools": [{"name": "a"}]}),
                    "metadata": {"anchor_head_id": "a-user"},
                },
                {"id": "head-a", "seq": 5, "role": "llm", "predecessor": "a-user"},
                {"id": "b-user", "seq": 6, "role": "user", "predecessor": "root"},
                {"id": "sp-b", "seq": 7, "name": "context/system_prompt", "output": "B",
                 "metadata": {}},
                {
                    "id": "ts-b", "seq": 8, "name": "context/tool_snapshot",
                    "output": json.dumps({"tools": [{"name": "b"}]}),
                    "metadata": {"anchor_head_id": "b-user"},
                },
                {"id": "head-b", "seq": 9, "role": "llm", "predecessor": "b-user"},
            ]

        def get_branch(self, _session_id, head_id=None):
            branches = {
                "head-a": [{"id": "root"}, {"id": "a-user"}, {"id": "head-a"}],
                "head-b": [{"id": "root"}, {"id": "b-user"}, {"id": "head-b"}],
            }
            return branches.get(head_id, [])

        def get_session(self, _session_id):
            return {"head_id": "head-b"}

    store = FakeStore()
    assert latest_recorded_prompt(store, "session", "head-a") == "A"
    assert latest_recorded_prompt(store, "session", "head-b") == "B"
    assert latest_recorded_tool_snapshot(store, "session", "head-a") == {
        "tools": [{"name": "a"}],
    }
    assert latest_recorded_tool_snapshot(store, "session", "head-b") == {
        "tools": [{"name": "b"}],
    }
    assert latest_recorded_prompt(store, "session", "missing") is None
    assert latest_recorded_tool_snapshot(store, "session", "missing") is None


def test_empty_recorded_tool_snapshot_does_not_read_live_registry(monkeypatch):
    class FakeDB:
        def get_branch(self, _session_id, _head_id=None):
            return [{"id": "head", "role": "user", "content": "question"}]

        def get_nodes(self, _session_id):
            return [{
                "id": "snapshot", "seq": 2, "name": "context/tool_snapshot",
                "output": json.dumps({"tools": []}),
                "metadata": {"anchor_head_id": "head"},
            }]

        def get_session(self, _session_id):
            return {"head_id": "head", "model": ""}

    import openprogram.agent.session_db as session_db
    import openprogram.context.breakdown as breakdown
    import openprogram.programs as programs
    import openprogram.skills.loader as skill_loader
    import openprogram.memory as memory

    monkeypatch.setattr(session_db, "default_db", lambda: FakeDB())
    monkeypatch.setattr(breakdown, "compute_call_breakdown", lambda **_kwargs: {
        "messages": 1, "system_prompt": 0, "skills": 0, "memory": 0,
        "tools_schema": 0, "tools_deferred_catalog": 0, "mcp_tools": 0,
        "input_used": 1, "tools": [],
    })
    monkeypatch.setattr(programs, "agent_tools", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("an empty recorded snapshot is still authoritative")
    ))
    monkeypatch.setattr(skill_loader, "list_skills", lambda: [])
    monkeypatch.setattr(memory, "get_backend", lambda: SimpleNamespace(system_prompt=lambda: ""))

    result = stats.compute_breakdown("session", head_id="head")

    assert result["tools_source"] == "recorded_snapshot"
    assert result["tools"] == []


def test_approval_wrapped_mcp_tool_keeps_server_in_recorded_snapshot(monkeypatch):
    from openprogram.agent.permissions.approval import wrap_with_approval
    from openprogram.agent.types import AgentTool
    from openprogram.context.tool_snapshot_node import record_tool_snapshot

    async def execute(_call_id, _args, _cancel, _on_update):
        return None

    tool = AgentTool(
        name="mcp_read", label="MCP read", description="read docs",
        parameters={"type": "object"}, execute=execute,
    )
    tool._mcp_server = "docs"
    wrapped = wrap_with_approval(
        tool,
        SimpleNamespace(session_id="session", permission_mode="ask", source="web"),
        lambda _event: None,
    )

    captured = []

    class FakeWriter:
        def __init__(self, _store, _session_id):
            pass

        def append(self, node):
            captured.append(node)

    class FakeStore:
        def get_nodes(self, _session_id):
            return []

        def get_session(self, _session_id):
            return {"head_id": "head"}

        def get_branch(self, _session_id, head_id=None):
            return [{"id": "head"}] if head_id == "head" else []

    import openprogram.context.budget as budget
    import openprogram.store as store_module
    monkeypatch.setattr(store_module, "SessionNodeWriter", FakeWriter)
    monkeypatch.setattr(budget, "estimate_tools_breakdown", lambda _tools: [{
        "name": "mcp_read", "tokens": 12, "deferred": False,
    }])

    assert record_tool_snapshot(FakeStore(), "session", [wrapped])
    assert json.loads(captured[0].output)["tools"][0]["server"] == "docs"


def test_non_advancing_turn_records_snapshots_on_its_bound_branch(monkeypatch):
    from openprogram.context.system_prompt_node import record_system_prompt
    from openprogram.context.tool_snapshot_node import record_tool_snapshot
    from openprogram.store import _current_turn_id

    nodes = [
        {"id": "root", "seq": 1, "role": "user"},
        {"id": "parent-head", "seq": 2, "role": "llm", "predecessor": "root"},
        {"id": "child-head", "seq": 3, "role": "llm", "predecessor": "root"},
    ]

    class FakeStore:
        def get_nodes(self, _session_id):
            return nodes

        def get_session(self, _session_id):
            return {"head_id": "parent-head"}

        def get_branch(self, _session_id, head_id=None):
            branches = {
                "parent-head": [{"id": "root"}, {"id": "parent-head"}],
                "child-head": [{"id": "root"}, {"id": "child-head"}],
            }
            return branches.get(head_id, [])

    class FakeWriter:
        def __init__(self, _store, _session_id):
            pass

        def append(self, node):
            value = node.to_dict()
            value["seq"] = len(nodes) + 1
            nodes.append(value)

    import openprogram.context.budget as budget
    import openprogram.store as store_module
    monkeypatch.setattr(store_module, "SessionNodeWriter", FakeWriter)
    monkeypatch.setattr(budget, "estimate_tools_breakdown", lambda _tools: [])

    token = _current_turn_id.set("child-head")
    try:
        assert record_system_prompt(FakeStore(), "session", "CHILD PROMPT")
        assert record_tool_snapshot(FakeStore(), "session", [])
    finally:
        _current_turn_id.reset(token)

    snapshot_nodes = [
        node for node in nodes
        if str(node.get("name") or "").startswith("context/")
    ]
    assert len(snapshot_nodes) == 2
    assert {node["metadata"]["anchor_head_id"] for node in snapshot_nodes} == {
        "child-head",
    }
    assert latest_recorded_prompt(FakeStore(), "session", "parent-head") is None
    assert latest_recorded_prompt(FakeStore(), "session", "child-head") == "CHILD PROMPT"
    assert latest_recorded_tool_snapshot(FakeStore(), "session", "parent-head") is None
    assert latest_recorded_tool_snapshot(FakeStore(), "session", "child-head") == {
        "tools": [],
    }
