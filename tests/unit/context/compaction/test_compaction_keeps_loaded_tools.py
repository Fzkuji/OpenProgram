"""A summary carries the tools its turns loaded, by name.

A request loads what its own messages loaded (``shared.tool_loads.loaded_tools``):
the load records of its tool results. A summary takes the place of the
turns it covers and of their records, so the compaction writes the names
of the tools they had loaded into the summary node (``carried_tool_names``),
most recently used first and cut to a share of the window. The summary
message carries them, so every provider still loads them: it offers them in
its tools array and reads the summary first.
"""
from __future__ import annotations

import json

import pytest

from openprogram.models.providers.shared.tool_loads import loaded_tools
from openprogram.models.providers.types import Context, Model, Tool
from openprogram.runtime.context.compaction.carried_tools import (
    CARRIED_TOOL_NAMES, carried_tool_names, carried_tools,
)
from openprogram.runtime.context.session_dag.nodes import render_context
from openprogram.runtime.context.compaction.persistence import SUMMARY_NODE_NAME
from openprogram.runtime.context.session_dag.render import render_dag_messages
from openprogram.state.store.session.nodes import Call, Graph, ROLE_CODE, ROLE_LLM, ROLE_USER

MODEL = Model(id="gpt-5.5", name="gpt-5.5", api="openai-responses", provider="openai",
              base_url="https://example.invalid")
GEMINI = Model(id="gemini-3-pro", name="gemini-3-pro", api="google-generative-ai",
               provider="google", base_url="https://example.invalid")


def _tool(name: str) -> Tool:
    return Tool(name=name, description=f"The {name} tool.",
                parameters={"type": "object", "properties": {"x": {"type": "string"}}})


READ, SEARCH, SCHEDULER = _tool("read"), _tool("tool_search"), _tool("scheduler")
SCHEMA = "Loaded 1 deferred tool. Their full schemas follow.\n<functions>...</functions>"


def _loads(*names: str) -> dict:
    """The load record ``tool_search`` writes for ``names`` (``shared.tool_loads``)."""
    return {"loads": [{"name": n, "definition": {
        "name": n, "description": f"The {n} tool.", "parameters": SCHEDULER.parameters}}
        for n in names]}


def _conversation(*, loads: "dict | None" = None, is_error: bool = False):
    """Turn 1 searches for a tool and calls it; turns 2-4 are plain chat."""
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="List my jobs.", predecessor=None))
    llm = g.add(Call(role=ROLE_LLM, output="You have none.", predecessor=u.id))
    search = g.add(Call(role=ROLE_CODE, name="tool_search", caller=llm.id,
                        input={"select": "scheduler"}, output=SCHEMA,
                        metadata={"expose": "full", "tool_call_id": "call_s",
                                  "details": _loads("scheduler") if loads is None else loads,
                                  **({"is_error": True} if is_error else {})}))
    g.add(Call(role=ROLE_CODE, name="scheduler", caller=llm.id, input={"x": "list"},
               output="no jobs", metadata={"expose": "full", "tool_call_id": "call_j"}))
    covered = [u.id, llm.id]
    prev = llm.id
    for i in range(3):
        u = g.add(Call(role=ROLE_USER, output=f"question {i}", predecessor=prev))
        llm = g.add(Call(role=ROLE_LLM, output=f"answer {i}", predecessor=u.id))
        if i == 0:
            covered += [u.id, llm.id]
        prev = llm.id
    return g, search, covered


def _compact(g: Graph, covered: list[str], carried: "list[str] | None" = None) -> Call:
    metadata: dict = {"covers_ids": covered}
    if carried is not None:
        metadata[CARRIED_TOOL_NAMES] = carried
    return g.add(Call(role=ROLE_LLM, name=SUMMARY_NODE_NAME,
                      output="[Previous conversation summary]\nThe user listed jobs.",
                      predecessor=None, metadata=metadata))


def _tip(g: Graph) -> str:
    return max((n for n in g if not n.caller and n.name != SUMMARY_NODE_NAME), key=lambda n: n.seq).id


def _messages(g: Graph, model=MODEL) -> list:
    ids = render_context(g, head_id=_tip(g), frame_entry_seq=-1)
    return render_dag_messages(g, ids, reasoning_for=model)


def test_a_summary_written_before_names_were_recorded_carries_what_it_covers():
    g, _search, covered = _conversation()
    summary = _compact(g, covered)  # no carried_tool_names
    assert carried_tools(g, summary)[0]["name"] == "scheduler"
    assert loaded_tools(_messages(g)) == ["scheduler"]


def test_a_summary_that_carries_nothing_loads_nothing():
    g, _search, covered = _conversation()
    _compact(g, covered, [])
    assert loaded_tools(_messages(g)) == []


@pytest.mark.parametrize("loads", [{}, {"loads": []}])
def test_a_search_that_loaded_nothing_carries_nothing(loads):
    g, _search, covered = _conversation(loads=loads)
    assert carried_tool_names(g.nodes.values(), covered) == []


def test_carried_names_are_most_recently_used_first_and_cut_to_the_window():
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="go", predecessor=None))
    llm = g.add(Call(role=ROLE_LLM, output="done", predecessor=u.id))

    def code(name, **md):
        return g.add(Call(role=ROLE_CODE, name=name, caller=llm.id, input={}, output="ok",
                          metadata={"tool_call_id": f"c{len(g.nodes)}", **md}))

    code("tool_search", details=_loads("alpha", "beta"))
    code("tool_search", details=_loads("gamma"))
    code("alpha")  # used after gamma was loaded
    # A nested call's load is its own run's, not the conversation's.
    g.add(Call(role=ROLE_CODE, name="tool_search", caller=g.nodes[next(reversed(g.nodes))].id,
               input={}, output="ok", metadata={"tool_call_id": "nested", "details": _loads("delta")}))
    covered = [u.id, llm.id]
    assert carried_tool_names(g.nodes.values(), covered) == ["alpha", "gamma", "beta"]
    one = len(json.dumps(_loads("alpha")["loads"][0]["definition"])) // 4 + 1
    window = int((2 * one + 1) / 0.05) + 1  # room for two definitions
    assert carried_tool_names(g.nodes.values(), covered, window) == ["alpha", "gamma"]


def test_the_compaction_writes_the_carried_names(monkeypatch):
    from openprogram.runtime.context import session_db
    from openprogram.runtime.context.compaction.persistence import Persister

    g, _search, covered = _conversation()
    written: list[dict] = []

    class _Db:
        def get_nodes(self, _sid):
            return list(g.nodes.values())

        def append_message(self, _sid, row):
            written.append(row)

    monkeypatch.setattr(session_db, "default_db", lambda: _Db())
    history = [{"id": nid, "timestamp": 1.0} for nid in covered] + [{"id": "kept", "timestamp": 2.0}]
    assert Persister().insert_summary_node("s1", summary_text="recap", cut_idx=len(covered),
                                           history=history, context_window=200_000)
    assert written[0]["extra"][CARRIED_TOOL_NAMES] == ["scheduler"]


def test_a_bounded_tail_keeps_the_summary_and_notes_after_it():
    """A background turn reads the summary and the newest steps; the note
    that older messages are left out comes right after the summary."""
    from openprogram.runtime.context.engine import DefaultContextEngine
    from openprogram.runtime.context.session_dag.tail import OMITTED_NOTE, bounded_tail

    g, _search, covered = _conversation()
    summary = _compact(g, covered, ["scheduler"])
    ids = render_context(g, head_id=_tip(g), frame_entry_seq=-1)
    kept, omitted = bounded_tail(g, ids, 1)
    assert omitted and kept == [summary.id]
    messages = DefaultContextEngine._with_omitted_note(
        g, kept, render_dag_messages(g, kept, reasoning_for=MODEL))
    assert messages[0].content[0].text.startswith("[Previous conversation summary]")
    assert messages[1].content[0].text == OMITTED_NOTE
    assert loaded_tools(messages) == ["scheduler"]


def test_another_provider_reads_the_summary_first_and_offers_the_tool_in_its_array():
    """Gemini offers a loaded tool in its tools array; a search pair would
    open its request on a function call, which it refuses."""
    from openprogram.models.providers.adapters.google.google import _build_contents
    from openprogram.programs import split_tools_for_dispatch
    from openprogram.programs._host.runtime import Tier, set_tier

    g, _search, covered = _conversation()
    summary = _compact(g, covered, ["scheduler"])
    messages = _messages(g, GEMINI)
    contents = _build_contents(Context(system_prompt="You help.", messages=messages, tools=[]), GEMINI)
    assert contents[0].role == "model"
    assert [p.text for p in contents[0].parts] == [summary.output]
    assert not [p for c in contents for p in c.parts if p.function_call or p.function_response]

    from openprogram.models.providers.types import AgentTool

    async def _run(*_args):
        return None
    deferred = AgentTool(**SCHEDULER.model_dump(), label="scheduler", execute=_run)
    set_tier(deferred, Tier.DEFERRED)
    provider, _catalog = split_tools_for_dispatch([deferred], loaded_tools(messages))
    assert [t.name for t in provider] == ["scheduler"]
