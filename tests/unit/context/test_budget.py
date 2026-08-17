from __future__ import annotations

from openprogram.context.budget import (
    BudgetAllocator,
    estimate_tools_breakdown,
)
from openprogram.context.types import UsageState


class _FakeTool:
    """budget 只读 .schema/.spec、.name、_defer —— 无需真 AgentTool。"""
    def __init__(self, name, schema, defer=False):
        self.name = name
        self.description = ""
        self.schema = schema
        self._defer = defer


def _tool(name, defer=False):
    tool = _FakeTool(
        name,
        {"name": name, "description": "x" * 40, "parameters": {"type": "object"}},
        defer=defer,
    )
    tool.description = "x" * 40
    return tool


def test_breakdown_sum_equals_estimate_tools():
    """per-tool 之和必须等于旧的加总口径（自洽）。"""
    tools = [_tool("bash"), _tool("web_search", defer=True), _tool("read")]
    per = estimate_tools_breakdown(tools)
    assert sum(x["tokens"] for x in per) == BudgetAllocator._estimate_tools(tools)


def test_breakdown_marks_deferred_and_names():
    tools = [_tool("bash"), _tool("web_search", defer=True)]
    per = estimate_tools_breakdown(tools)
    by = {x["name"]: x for x in per}
    assert by["bash"]["deferred"] is False
    assert by["web_search"]["deferred"] is True
    assert by["bash"]["tokens"] > 0


def test_breakdown_empty():
    assert estimate_tools_breakdown([]) == []


def test_default_engine_budgets_only_provider_resident_tools(monkeypatch):
    from types import SimpleNamespace
    import openprogram.context.engine as engine_module
    from openprogram.context.engine import DefaultContextEngine
    from openprogram.programs import (
        install_loaded_deferred, release_turn_tools, split_tools_for_dispatch,
    )

    tools = [_tool("resident"), _tool("deferred", defer=True)]
    release_turn_tools()
    install_loaded_deferred()
    provider_tools, catalog = split_tools_for_dispatch(tools)
    assert [tool.name for tool in provider_tools] == ["resident"]
    assert [name for name, _description in catalog] == ["deferred"]

    context_engine = DefaultContextEngine(
        usage_tracker=SimpleNamespace(get=lambda _session_id: UsageState()),
        references=SimpleNamespace(build=lambda _history: SimpleNamespace(
            cited_tool_use_ids=set(),
        )),
    )
    monkeypatch.setattr(context_engine, "_build_messages_from_dag", lambda **_kwargs: [])
    monkeypatch.setattr(engine_module, "real_context_window", lambda _model: 100_000)

    prep = context_engine.prepare(
        agent=SimpleNamespace(), session={"id": "session"}, history=[],
        model=SimpleNamespace(), tools=tools, system_prompt="prompt",
    )

    assert prep.budget.tools_schema == BudgetAllocator._estimate_provider_tools(provider_tools)


class _ParametersTool:
    """The shape most registered tools actually have: the JSON schema
    lives on ``.parameters``, not ``.schema`` / ``.spec``."""

    def __init__(self, name, parameters):
        self.name = name
        self.parameters = parameters
        self._defer = False


def test_parameters_shaped_tools_are_priced_off_their_real_schema():
    """Regression: the fallback chain read only .schema/.spec, so every
    ``.parameters``-shaped tool fell through to the 20-token 'unknown
    tool' guess — pricing a ~6.8k-token toolset at ~690."""
    schema = {
        "type": "object",
        "properties": {
            f"field_{i}": {"type": "string", "description": "y" * 60}
            for i in range(20)
        },
    }
    tool = _ParametersTool("big_tool", schema)
    priced = estimate_tools_breakdown([tool])[0]["tokens"]

    assert priced > 20, "must not fall back to the unknown-tool guess"
    # Same number the equivalent .schema-shaped tool gets.
    assert priced == estimate_tools_breakdown(
        [_FakeTool("big_tool", schema)])[0]["tokens"]


def test_unknown_shaped_tool_still_falls_back():
    class _Opaque:
        name = "mystery"
        _defer = False

    assert estimate_tools_breakdown([_Opaque()])[0]["tokens"] == 20


# --- Accuracy against the real wire ----------------------------------------
#
# The two halves of the estimate used to be wrong in OPPOSITE directions —
# resident tools priced without their description (588 est / 4567 real),
# deferred tools priced with a full description the catalog never sends
# (4665 est / 183 real). The errors nearly cancelled, so the TOTAL looked
# sane while both components were off by ~8x. These tests pin each half
# separately so a future cancellation can't hide again.

_TOLERANCE = 0.15


def _wire_resident_tokens(tools) -> int:
    """Exactly what the Anthropic provider puts on the wire per tool."""
    import json
    from openprogram.context.tokens import _text_tokens
    from openprogram.providers._schema import normalize_for

    return sum(
        _text_tokens(json.dumps(
            {"name": t.name, "description": t.description,
             "input_schema": normalize_for(None, t.parameters, None)},
            ensure_ascii=False, default=str,
        ))
        for t in tools
    )


def test_resident_estimate_tracks_the_real_tools_array():
    from openprogram.programs import (
        agent_tools, install_loaded_deferred, release_turn_tools,
        split_tools_for_dispatch,
    )
    release_turn_tools()
    install_loaded_deferred()
    resident, _ = split_tools_for_dispatch(list(agent_tools(toolset="full")))
    assert resident, "expected a non-empty resident toolset"

    real = _wire_resident_tokens(resident)
    est = BudgetAllocator._estimate_tools(resident)
    assert abs(est - real) / real < _TOLERANCE, (
        f"resident estimate {est} vs real {real} "
        f"({100 * (est - real) / real:+.1f}%)"
    )


def test_deferred_catalog_estimate_tracks_the_rendered_block():
    """The catalog only sends bare names — price the block, not the docs."""
    from openprogram.context.breakdown import _catalog_tokens
    from openprogram.context.tokens import _text_tokens
    from openprogram.programs import (
        agent_tools, deferred_catalog_text, install_loaded_deferred,
        release_turn_tools, split_tools_for_dispatch,
    )
    release_turn_tools()
    install_loaded_deferred()
    _, catalog = split_tools_for_dispatch(list(agent_tools(toolset="full")))
    assert catalog, "expected deferred tools"

    real = _text_tokens(deferred_catalog_text(catalog))
    est = _catalog_tokens(catalog)
    assert abs(est - real) / real < _TOLERANCE, (
        f"catalog estimate {est} vs real {real} "
        f"({100 * (est - real) / real:+.1f}%)"
    )


def test_resident_tool_price_includes_its_description():
    """Root cause of the 8x undercount: ``t.schema`` on a pydantic
    AgentTool resolves to ``BaseModel.schema`` — the deprecated bound
    METHOD — so the old ``schema or spec or parameters`` chain priced a
    repr of a method object and dropped the description entirely."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}

    class _Described:
        name = "described"
        parameters = schema
        description = "z" * 2000
        _defer = False

    class _Bare:
        name = "described"
        parameters = schema
        description = ""
        _defer = False

    with_desc = estimate_tools_breakdown([_Described()])[0]["tokens"]
    without = estimate_tools_breakdown([_Bare()])[0]["tokens"]
    assert with_desc > without + 300, (
        "description must be part of a resident tool's price"
    )


def test_deferred_tool_price_ignores_its_description():
    class _Fat:
        name = "fat"
        description = "z" * 4000
        parameters = {"type": "object"}
        _defer = True

    class _Thin:
        name = "fat"
        description = ""
        parameters = {"type": "object"}
        _defer = True

    assert (estimate_tools_breakdown([_Fat()])[0]["tokens"]
            == estimate_tools_breakdown([_Thin()])[0]["tokens"]), (
        "the catalog sends names only — description must not be priced"
    )
