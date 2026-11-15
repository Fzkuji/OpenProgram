"""Old tool results are sent as their turn sent them; only compaction replaces them.

Aging (turning old results into one-line stubs) rewrote the cached prefix
each time its boundary moved. Codex, Claude Code, Pi and OpenCode 1.19 have
no such step, so the render has none: a result reads the same in every
later request, however many turns follow it.
"""
from __future__ import annotations

import importlib

import pytest

from openprogram.runtime.context.nodes import render_context
from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store.session.nodes import Call, Graph, ROLE_CODE, ROLE_LLM, ROLE_USER


def _session(n_turns: int, *, manifest: dict | None = None) -> Graph:
    g = Graph()
    prev = None
    for i in range(n_turns):
        u = g.add(Call(role=ROLE_USER, output=f"question {i}", predecessor=prev))
        metadata = {"render_manifest": manifest} if manifest else {}
        llm = g.add(Call(role=ROLE_LLM, output=f"answer {i}", predecessor=u.id, metadata=metadata))
        g.add(Call(role=ROLE_CODE, name="grep", caller=llm.id, input={"pattern": f"p{i}"},
                   output=f"a long tool result for turn {i}\n" * 5,
                   metadata={"expose": "full", "tool_call_id": f"tc{i}"}))
        prev = llm.id
    return g


def _tip(g: Graph) -> str:
    return max((n for n in g if not n.caller), key=lambda n: n.seq).id


def _render(g: Graph) -> list[str]:
    ids = render_context(g, head_id=_tip(g), frame_entry_seq=-1)
    return [repr(m.model_dump(exclude={"timestamp"})) for m in render_dag_messages(g, ids)]


def test_the_aging_modules_are_gone():
    for name in ("openprogram.runtime.context.aging", "openprogram.runtime.context.tool_aging"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_another_turn_after_fifteen_leaves_the_prefix_alone():
    before = _render(_session(15))
    after = _render(_session(16))
    assert after[:len(before)] == before
    assert all("[aged]" not in item for item in after)
    assert "a long tool result for turn 0" in "\n".join(after)


def test_a_reply_with_an_old_aging_manifest_still_renders_whole():
    stamped = _render(_session(6, manifest={"policy_version": "aging-v2", "aged_before_seq": 9,
                                            "spilled": []}))
    assert stamped == _render(_session(6))
