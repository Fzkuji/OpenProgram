"""Optional tool params (those with a Python default) must accept ``null`` in
their JSON schema — the model naturally passes null to mean "unspecified",
and the impl already treats None/"" as "use the default". Before this fix a
bare {"type":"string"} rejected null at the validator and the whole tool call
failed (observed: task's agent_id=None → "None is not of type 'string'").

Applies to EVERY tool, not just task.
"""
from __future__ import annotations

from typing import Optional

from openprogram.functions._runtime import (
    _build_parameters_schema,
    _allow_null,
    _widen_optionals_to_null,
)


def _type_of(schema_prop):
    return schema_prop.get("type")


def _accepts_null(schema_prop) -> bool:
    t = schema_prop.get("type")
    if t == "null":
        return True
    if isinstance(t, list):
        return "null" in t
    if t is None:
        if "oneOf" in schema_prop:
            return any(v.get("type") == "null" for v in schema_prop["oneOf"])
        if "enum" in schema_prop:
            return None in schema_prop["enum"]
        return True  # free-form
    return False


# ── generator marks required; the exit-point widens optionals ──
# Architecture (mirrors opencode's provider/transform.ts `schema()`): the
# generator only decides `required`; widening optionals to accept null happens
# once, centrally, at the schema exit point (_widen_optionals_to_null), so it
# covers BOTH generated and hand-written schemas.

def test_generator_marks_only_no_default_as_required():
    def fn(prompt: str, agent_id: str = "", wait: bool = True):
        """
        prompt: p
        agent_id: a
        wait: w
        """
    schema = _build_parameters_schema(fn)
    # required = only the no-default param; generator does NOT add null itself
    assert schema.get("required") == ["prompt"]


def test_exit_point_widens_optionals_not_required():
    # The full pipeline: generate → widen. Optionals accept null, required
    # stays strict. This is exactly what a tool's final .parameters looks like.
    def fn(prompt: str, agent_id: str = "", wait: bool = True):
        """
        prompt: p
        agent_id: a
        wait: w
        """
    schema = _widen_optionals_to_null(_build_parameters_schema(fn))
    props = schema["properties"]
    assert _accepts_null(props["agent_id"]), props["agent_id"]
    assert _accepts_null(props["wait"]), props["wait"]
    assert props["prompt"].get("type") == "string"  # required → not widened


def test_exit_point_widens_handwritten_schema():
    # Hand-written schema (the ~80 params the generator never saw) is covered
    # by the same exit point.
    handwritten = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "command": {"type": "string", "description": "optional"},
        },
        "required": ["action"],
    }
    out = _widen_optionals_to_null(handwritten)
    assert out["properties"]["action"].get("type") == "string"  # required
    assert _accepts_null(out["properties"]["command"])           # optional


# ── _allow_null unit behaviour ──

def test_allow_null_widens_string():
    assert _allow_null({"type": "string"})["type"] == ["string", "null"]


def test_allow_null_idempotent():
    once = _allow_null({"type": "string"})
    twice = _allow_null(once)
    assert once == twice


def test_allow_null_empty_schema_untouched():
    assert _allow_null({}) == {}


def test_allow_null_oneof_adds_null_variant():
    out = _allow_null({"oneOf": [{"type": "string"}, {"type": "integer"}]})
    assert any(v.get("type") == "null" for v in out["oneOf"])


# ── the actual bug: the task tool's agent_id ──

def test_task_tool_agent_id_accepts_null():
    from openprogram.functions import agent_tools
    task = next((t for t in agent_tools(toolset="full") if t.name == "task"), None)
    assert task is not None, "task tool must be registered"
    params = task.parameters
    assert params, "task tool must expose a parameters schema"
    props = params["properties"]
    assert "agent_id" in props
    assert _accepts_null(props["agent_id"]), props["agent_id"]
    # agent_id is optional → not in required
    assert "agent_id" not in params.get("required", [])
