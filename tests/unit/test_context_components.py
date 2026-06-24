"""Context refactor — registry assembly correctness.

Step 1 migrated the 5 system-prompt blocks to ContextComponents. Step 2 moved
workspace files to L1 (project-level) while identity/inline/skills/memory stay
L0. The system prompt is assembled L0-then-L1, so order is now:
    identity → inline → skills → memory → workspace(L1)
(workspace moved to the end by design — L0 stable prefix first, see
docs/design/context/context-composition.md §三).

We verify the assembler's layer+order logic directly with stub components, so
the test doesn't depend on real workspace files / skills / memory on disk.
"""
import openprogram.context.components as comp
from openprogram.context.components import (
    ContextComponent, register, assemble, build_system_prompt,
)


def _restore_registry():
    """Snapshot + restore the real registry so stub tests don't leak."""
    import copy
    return copy.deepcopy(comp._REGISTRY)


def test_assemble_orders_by_layer_then_order():
    saved = _restore_registry()
    try:
        comp._REGISTRY = {"L0": [], "L1": [], "L2": []}
        register(ContextComponent("a", "L0", 20, lambda x: "A"))
        register(ContextComponent("b", "L0", 10, lambda x: "B"))   # lower order first
        register(ContextComponent("c", "L1", 10, lambda x: "C"))
        register(ContextComponent("empty", "L0", 5, lambda x: ""))  # dropped
        register(ContextComponent("off", "L1", 5, lambda x: "X",
                                  condition=lambda x: False))        # dropped
        parts = assemble({}, ["L0", "L1"])
        # L0 by order (b<a), empty dropped; then L1 (c), off dropped.
        assert parts == ["B", "A", "C"]
    finally:
        comp._REGISTRY = saved


def test_real_registry_has_expected_layers():
    # identity/inline/skills/memory in L0; workspace_files in L1.
    l0 = {c.name for c in comp._REGISTRY["L0"]}
    l1 = {c.name for c in comp._REGISTRY["L1"]}
    assert {"identity", "inline_prompt", "skills_index", "memory_global"} <= l0
    assert "workspace_files" in l1


def test_build_system_prompt_fence_and_identity_first():
    # identity is always present and first; output wrapped in the fence.
    out = build_system_prompt({"id": "main", "name": "bot"})
    assert out.startswith("── Agent prompt ──\n")
    assert out.rstrip().endswith("── End of agent prompt ──")
    assert "You are bot (agent_id=main)." in out
    # identity appears before any later block boundary
    assert out.index("You are bot") < out.index("End of agent prompt")


def test_environment_and_date_components_present():
    out = build_system_prompt({"id": "main", "name": "bot"})
    # environment block + day-granularity date are new L0 components.
    assert "<environment>" in out and "</environment>" in out
    assert "OS:" in out
    assert "Today is " in out
    # they sit at the L0 tail: after identity, before the closing fence.
    assert out.index("You are bot") < out.index("<environment>")


def test_tool_enforcement_always_present():
    out = build_system_prompt({"id": "main", "name": "bot"})
    assert "<tool_use>" in out


def test_model_guidance_conditional_on_provider():
    # google provider → guidance present (absolute paths)
    g = build_system_prompt({"id": "main", "name": "bot",
                             "model": {"provider": "google"}})
    assert "<execution_guidance>" in g and "absolute paths" in g
    # anthropic → no extra guidance (empty row)
    a = build_system_prompt({"id": "main", "name": "bot",
                             "model": {"provider": "anthropic"}})
    assert "<execution_guidance>" not in a
    # unknown provider → no guidance
    u = build_system_prompt({"id": "main", "name": "bot"})
    assert "<execution_guidance>" not in u
