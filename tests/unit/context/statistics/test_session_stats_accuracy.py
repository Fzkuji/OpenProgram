from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.context.token_accounting import session_stats as stats
from openprogram.runtime.context.prompt_assembly.system_prompt_node import latest_recorded_prompt
from openprogram.runtime.context.prompt_assembly.tool_snapshot_node import latest_recorded_tool_snapshot


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
            return {"model": ""}

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

    import openprogram.runtime.context.session_db as session_db
    import openprogram.runtime.context.token_accounting.breakdown as breakdown
    import openprogram.extensions.skills.loader as skill_loader
    import openprogram.state.memory as memory
    import openprogram.programs._host.runtime as runtime

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

    import openprogram.runtime.context.session_db as session_db
    import openprogram.runtime.context.token_accounting.breakdown as breakdown
    import openprogram.programs as programs
    import openprogram.extensions.skills.loader as skill_loader
    import openprogram.state.memory as memory

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

    import openprogram.runtime.context.session_db as session_db
    import openprogram.runtime.context.token_accounting.breakdown as breakdown
    import openprogram.programs as programs
    import openprogram.extensions.skills.loader as skill_loader
    import openprogram.state.memory as memory

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
    from openprogram.runtime.permissions.approvals.approval import wrap_with_approval
    from openprogram.models.providers.types import AgentTool
    from openprogram.runtime.context.prompt_assembly.tool_snapshot_node import record_tool_snapshot

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

    import openprogram.runtime.context.token_accounting.budget as budget
    import openprogram.state.store as store_module
    monkeypatch.setattr(store_module, "SessionNodeWriter", FakeWriter)
    monkeypatch.setattr(budget, "estimate_tools_breakdown", lambda _tools: [{
        "name": "mcp_read", "tokens": 12, "deferred": False,
    }])

    assert record_tool_snapshot(FakeStore(), "session", [wrapped])
    assert json.loads(captured[0].output)["tools"][0]["server"] == "docs"


def test_non_advancing_turn_records_snapshots_on_its_bound_branch(monkeypatch):
    from openprogram.runtime.context.prompt_assembly.system_prompt_node import record_system_prompt
    from openprogram.runtime.context.prompt_assembly.tool_snapshot_node import record_tool_snapshot
    from openprogram.state.store import _current_turn_id

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

    import openprogram.runtime.context.token_accounting.budget as budget
    import openprogram.state.store as store_module
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


def test_tool_snapshot_never_creates_a_conversation(tmp_path):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.context.prompt_assembly.tool_snapshot_node import record_tool_snapshot

    db = SessionDB(tmp_path / "sessions.sqlite")

    assert record_tool_snapshot(db, "op-0123456789abcdef", []) is None
    assert db.get_session("op-0123456789abcdef") is None
    assert db.list_sessions() == []


def test_recorded_prompt_carries_its_component_lengths(monkeypatch):
    from openprogram.runtime.context.prompt_assembly import components
    from openprogram.runtime.context.prompt_assembly.system_prompt_node import (
        latest_recorded_sections, record_system_prompt,
    )

    nodes: list = []

    class FakeStore:
        def get_nodes(self, _sid):
            return [{"name": n.name, "output": n.output, "metadata": n.metadata,
                     "id": n.id} for n in nodes]

    class Writer:
        def __init__(self, _store, _sid):
            pass

        def append(self, node):
            nodes.append(node)

    import openprogram.state.store as store_mod
    import openprogram.runtime.context.prompt_assembly.system_prompt_node as spn
    monkeypatch.setattr(store_mod, "SessionNodeWriter", Writer)
    monkeypatch.setattr(spn, "_active_head_id", lambda *_a: None)
    monkeypatch.setattr(spn, "_branch_anchor_ids", lambda *_a: None)

    text = components.build_system_prompt({"id": "main", "name": "bot"})
    assert record_system_prompt(FakeStore(), "s", text)
    sections = latest_recorded_sections(FakeStore(), "s")
    assert sections and sections[0]["name"] == "base"
    assert sum(r["chars"] for r in sections) + 2 * (len(sections) - 1) == len(text)


def test_system_rows_leave_memory_and_nested_categories_out():
    rows = stats._system_rows(
        [("base", "aaaa"), ("memory", "mmmm"), ("capabilities", "cccccc")],
        len, nested={"capabilities": 4},
    )
    assert [(r["name"], r["tokens"]) for r in rows] == [("base", 4), ("capabilities", 2)]
    assert rows[0]["text"] == "aaaa"


def test_an_older_prompt_still_finds_its_memory_fence():
    text = "You are main.\n\n<memory-context>\nfact\n</memory-context>\n\nold rules"
    pairs = stats._prompt_sections(text, None, object(), "main")
    names = [name for name, _ in pairs]
    assert "memory" in names and names[-1] == "other"


def test_detail_rows_add_up_to_their_calibrated_category():
    result = stats.finalize_breakdown(
        {"system_prompt": 100, "memory": 100, "messages": 0,
         "system_detail": [{"name": "base", "tokens": 70}, {"name": "environment", "tokens": 40}],
         "memory_detail": [{"path": "MEMORY.md", "tokens": 60}, {"path": "core.md", "tokens": 60}]},
        {"total_used": 100, "window": 1000},
    )
    assert sum(r["tokens"] for r in result["system_detail"]) == result["system_prompt"] == 50
    assert sum(r["tokens"] for r in result["memory_detail"]) == result["memory"] == 50


def test_message_detail_groups_by_kind_and_lists_the_largest():
    rows = [
        {"id": "u1", "role": "user", "_context_tokens": 10},
        {"id": "a1", "role": "assistant", "_context_tokens": 40},
        {"id": "s1", "role": "user", "name": "context/summary", "_context_tokens": 25},
    ]
    detail = stats._message_detail(rows, [{"id": "a1", "content": "long   reply"}])
    assert [(g["kind"], g["tokens"]) for g in detail["groups"]] == [
        ("assistant", 40), ("summary", 25), ("user", 10)]
    assert detail["top"][0] == {"id": "a1", "kind": "assistant", "tokens": 40,
                                "preview": "long reply"}


def test_tool_rows_add_up_to_their_tool_category():
    result = stats.finalize_breakdown(
        {"tools_schema": 100, "tools_deferred_catalog": 20, "mcp_tools": 0,
         "tools": [{"name": "a", "tokens": 80, "deferred": False},
                   {"name": "b", "tokens": 40, "deferred": False},
                   {"name": "c", "tokens": 20, "deferred": True}]},
        {"total_used": 60, "window": 1000},
    )
    loaded = [r["tokens"] for r in result["tools"] if not r["deferred"]]
    assert sum(loaded) == result["tools_schema"]
    assert [r["tokens"] for r in result["tools"] if r["deferred"]] == [result["tools_deferred_catalog"]]


def test_a_draft_chat_counts_the_agent_prompt_against_the_model_window(monkeypatch):
    import openprogram.runtime.conversation.model_tools as model_tools
    import openprogram.programs as programs
    import openprogram.models.providers.catalog.models as catalog

    monkeypatch.setattr(programs, "agent_tools", lambda **_kw: [])
    monkeypatch.setattr(model_tools, "load_agent_profile",
                        lambda _id: {"id": "main", "name": "Main"})
    monkeypatch.setattr(catalog, "get_model", lambda provider, model_id: (
        SimpleNamespace(context_window=400_000) if (provider, model_id) == ("p", "m") else None))

    bd = stats.compute_draft_breakdown("main", "p", "m")
    assert bd["draft"] and bd["context_window"] == 400_000
    assert bd["system_prompt"] > 0 and bd["messages"] == 0
    assert bd["system_detail"][0]["name"] == "base"


@pytest.mark.parametrize("ref, provider, expected", [
    ("openai/gpt-6.1-sol", None, ("openai", "gpt-6.1-sol")),
    ("openai:gpt-6.1-sol", None, ("openai", "gpt-6.1-sol")),
    ("openrouter/anthropic/claude-x", None, ("openrouter", "anthropic/claude-x")),
    ("ollama:llama3:8b", None, ("ollama", "llama3:8b")),
    ("gpt-6.1-sol", "openai", ("openai", "gpt-6.1-sol")),
    ("", "p", ("p", None)),
])
def test_one_parser_reads_every_stored_model_spelling(ref, provider, expected):
    from openprogram.models.providers.catalog.models import split_model_ref
    assert split_model_ref(ref, provider) == expected


def test_a_bare_id_holding_a_colon_resolves_under_its_provider(monkeypatch):
    import openprogram.models.providers.catalog.models as catalog
    model = SimpleNamespace(context_window=8_192)
    monkeypatch.setattr(catalog, "get_model", lambda provider, model_id: (
        model if (provider, model_id) == ("ollama", "llama3:8b") else None))
    assert catalog.find_model_ref("llama3:8b", "ollama") is model


def test_display_never_invents_a_window_compaction_keeps_its_default():
    from openprogram.runtime.context.token_accounting.tokens import known_context_window, real_context_window
    assert known_context_window(None) is None
    assert known_context_window(SimpleNamespace(context_window=None, max_tokens=8_000)) is None
    assert real_context_window(None) == 128_000
    assert stats.model_ref_window("nowhere/unknown") is None
