"""The deferred-tool catalog goes beside the message, as a delta (deferred-tools A2).

The whole catalog is sent on a conversation's first turn and on the first
turn after a summary; afterwards only what was added and removed. Built-in
deferred tools are listed by name, an MCP server's or an application's
tools as one line per namespace, and the note never passes 4 KiB. The
system prompt of the conversation's own turn holds no catalog, so a server
that connects never changes it.
"""
from __future__ import annotations

from openprogram.models.providers.types import AgentTool, AgentToolResult, TextContent
from openprogram.programs._host.runtime import (
    CATALOG_MAX_BYTES,
    catalog_note,
    deferred_catalog,
)


def _tool(name: str, *, server: str = "", deferred: bool = True) -> AgentTool:
    async def execute(call_id, args, cancel=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(name=name, label=name, description=f"The {name} tool.",
                     parameters={"type": "object", "properties": {}}, execute=execute)
    setattr(tool, "_defer", deferred)
    if server:
        setattr(tool, "_mcp_server", server)
    return tool


def test_built_in_tools_by_name_and_servers_by_namespace():
    catalog = deferred_catalog([
        _tool("scheduler"), _tool("read", deferred=False),
        _tool("linear__create_issue", server="linear"), _tool("linear__list_issues", server="linear"),
    ])
    assert catalog == {"scheduler": "",
                       "linear": "2 tools: linear__create_issue, linear__list_issues"}


def test_two_hundred_tools_fit_in_four_kib_and_say_how_many_were_left_out():
    tools = [_tool(f"builtin_tool_number_{n:03d}") for n in range(200)]
    note = catalog_note(None, deferred_catalog(tools))
    assert len(note.encode()) <= CATALOG_MAX_BYTES
    assert "more (find them with a keyword query)" in note
    assert "builtin_tool_number_000" in note


def test_a_namespace_line_is_cut_to_250_characters():
    tools = [_tool(f"big__tool_{n:02d}", server="big") for n in range(60)]
    assert len(deferred_catalog(tools)["big"]) <= 250


def test_a_server_that_connects_later_is_sent_as_added_only():
    before = deferred_catalog([_tool("scheduler")])
    after = deferred_catalog([_tool("scheduler"), _tool("linear__create_issue", server="linear")])
    note = catalog_note(before, after)
    assert "Added deferred tools:" in note and "- linear: 1 tools: linear__create_issue" in note
    assert "scheduler" not in note
    assert "Removed" not in note


def test_a_server_that_disconnects_is_sent_as_removed():
    before = deferred_catalog([_tool("scheduler"), _tool("linear__create_issue", server="linear")])
    note = catalog_note(before, deferred_catalog([_tool("scheduler")]))
    assert "Removed deferred tools:" in note and "linear" in note


def test_nothing_changed_sends_nothing_and_no_baseline_sends_everything():
    catalog = deferred_catalog([_tool("scheduler")])
    assert catalog_note(catalog, catalog) == ""
    full = catalog_note(None, catalog)
    assert full.startswith("<deferred-tools>\nDeferred tools you can load with tool_search:")
    assert "stays callable while its source is connected" in full


def test_the_first_turn_after_a_summary_has_no_catalog_in_view():
    """The baseline is the last catalog note the rendered history shows: a
    note on a turn a summary covers is not shown, so the whole catalog is
    sent again."""
    from openprogram.runtime.context import turn_context
    from openprogram.state.store.session.nodes import Call, Graph, ROLE_USER

    catalog = deferred_catalog([_tool("scheduler")])
    g = Graph()
    first = g.add(Call(role=ROLE_USER, output="hello", predecessor=None, metadata=turn_context.node_metadata(
        turn_context.put([], turn_context.SECTION, turn_context.CATALOG, catalog_note(None, catalog),
                         catalog=catalog))))
    later = g.add(Call(role=ROLE_USER, output="again", predecessor=first.id))

    shown = turn_context.last_sections(g, [first.id, later.id], turn_context.CONVERSATION)
    assert turn_context.catalog_shown(shown) == catalog
    assert catalog_note(turn_context.catalog_shown(shown), catalog) == ""
    after_summary = turn_context.last_sections(g, [later.id], turn_context.CONVERSATION)
    assert turn_context.catalog_shown(after_summary) is None
    assert catalog_note(None, catalog).startswith("<deferred-tools>")


def test_the_catalog_renders_after_the_memory_section():
    from openprogram.runtime.context import turn_context

    items = turn_context.put([], turn_context.SECTION, turn_context.CATALOG, "<deferred-tools>x",
                             catalog={})
    items = turn_context.put(items, turn_context.SECTION, "memory", "<memory-map-update>m")
    assert [entry["id"] for entry in items] == ["memory", turn_context.CATALOG]


def test_the_conversation_prompt_is_the_same_bytes_when_a_server_connects():
    from openprogram.runtime.context.components import build_system_prompt

    agent = {"id": "main", "system_prompt": "you are helpful"}
    plain = [_tool("read", deferred=False), _tool("tool_search", deferred=False), _tool("scheduler")]
    connected = [*plain, _tool("linear__create_issue", server="linear")]
    assert build_system_prompt(agent, tools=plain, catalog_beside=True) == \
        build_system_prompt(agent, tools=connected, catalog_beside=True)
    # A prompt of its own (an Agent method, runtime.exec) still lists them.
    assert "linear__create_issue" in build_system_prompt(agent, tools=connected)
