"""Runtime._call_via_providers (AgentSession path) builds its prompt
from the DAG when ``_store`` is installed.

Verified by spying on ``_render_history_messages``: when ``_store`` is
set, this returns a non-None list pulled from the graph; when no
store is installed, it returns None (legacy path kicks in).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM
from openprogram.store import GraphStoreShim, SessionStore, _store as _store_var


@pytest.fixture
def store(tmp_path: Path):
    """GraphStoreShim installed into ``_store`` for the test's duration."""
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = GraphStoreShim(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


@pytest.fixture
def rt() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


# ── No store: DAG path returns None ───────────────────────────────


def test_no_store_returns_none(rt):
    """Without an installed store the DAG helper must return None so
    the legacy render_messages path stays active."""
    msgs = rt._render_history_messages(
        content=[{"type": "text", "text": "hello"}],
    )
    assert msgs is None


# ── With store: DAG history shows up + current turn appended ──────


def test_with_store_builds_history_plus_current(rt, store):
    """Pre-fill the store with prior user + assistant messages.
    Calling the helper should return those + a fresh UserMessage
    built from ``content``."""
    store.append(Call(role=ROLE_USER, output="hi"))
    store.append(Call(role=ROLE_LLM, output="hello back"))

    msgs = rt._render_history_messages(
        content=[{"type": "text", "text": "what next?"}],
    )
    assert msgs is not None
    roles = [m.role for m in msgs]
    # prior user + prior llm + fresh user (current turn)
    assert roles == ["user", "assistant", "user"]
    last_text = msgs[-1].content[0].text
    assert last_text == "what next?"


def test_empty_store_yields_only_current_turn(rt, store):
    """Empty store: history is empty; current-turn user message
    still gets synthesized."""
    msgs = rt._render_history_messages(
        content=[{"type": "text", "text": "first ping"}],
    )
    assert msgs is not None
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content[0].text == "first ping"


def test_multiple_text_blocks_keep_separate_parts(rt, store):
    """``_build_pi_context`` no longer concatenates multiple text
    blocks. Each block becomes its own ``TextContent`` part so that
    image / video / audio blocks interleaved with text survive in
    their original order (concatenation used to drop non-text
    blocks). The test verifies the new contract: two text blocks
    appear as two parts."""
    msgs = rt._render_history_messages(content=[
        {"type": "text", "text": "line 1"},
        {"type": "text", "text": "line 2"},
    ])
    parts = msgs[-1].content
    assert len(parts) == 2
    assert parts[0].text == "line 1"
    assert parts[1].text == "line 2"


# ── Inside an @agentic_function frame ───────────────────────────────


def test_dag_prompt_inside_io_function_frame(rt, store):
    """Simulate the state ``_call_via_providers`` would see when
    ``runtime.exec`` is invoked from inside an @agentic_function
    body with expose='io':

      n0  user        "find weather"
      n1  llm         "let me check"          (response to user)
      n2  code/plan   running, expose=io      (placeholder, function entered)

    Then the body calls runtime.exec. _call_id is set to n2.id.
    """
    from openprogram.agentic_programming.function import _call_id
    from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE

    store.append(Call(role=ROLE_USER, output="find weather"))
    store.append(Call(role=ROLE_LLM, output="let me check"))
    plan_node = Call(
        role=ROLE_CODE,
        name="plan",
        input={"task": "weather"},
        output=None,
        metadata={"expose": "io", "status": "running"},
    )
    store.append(plan_node)

    token = _call_id.set(plan_node.id)
    try:
        msgs = rt._render_history_messages(
            content=[{"type": "text", "text": "step 1"}],
        )
    finally:
        _call_id.reset(token)

    assert msgs is not None
    roles = [m.role for m in msgs]
    # user + assistant from chat + (user from code call sig, no assistant
    # because output=None) + current turn
    assert "user" in roles
    assert "assistant" in roles
    # Current turn is the last message. It carries an "[Execution context]"
    # situational prefix block naming the current function `plan` and warning
    # against self-recursion (so the inner model knows which function it is
    # running inside), followed by the exec content "step 1".
    last_texts = [c.text for c in msgs[-1].content]
    assert any("step 1" == t for t in last_texts)
    assert any(("[Execution context]" in t and "`plan`" in t) for t in last_texts)


def test_render_range_callers_zero_hides_history(rt, store):
    """When inside a frame with render_range={'callers':0}, prior chat
    history is walled off — only in-frame nodes + current turn appear."""
    from openprogram.agentic_programming.function import _call_id
    from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE

    store.append(Call(role=ROLE_USER, output="prior chat user"))
    store.append(Call(role=ROLE_LLM, output="prior chat reply"))
    isolated = Call(
        role=ROLE_CODE, name="isolated", input={}, output=None,
        metadata={"expose": "io", "status": "running",
                  "render_range": {"callers": 0, "subcalls": 99}},
    )
    store.append(isolated)

    token = _call_id.set(isolated.id)
    try:
        msgs = rt._render_history_messages(
            content=[{"type": "text", "text": "isolated turn"}],
        )
    finally:
        _call_id.reset(token)

    assert msgs is not None
    # Flatten every text block of every message (the current turn now
    # carries an "[Execution context]" situational prefix block before the content).
    all_texts = [
        c.text for m in msgs for c in (m.content or [])
        if hasattr(c, "text")
    ]
    # Prior chat messages must NOT appear
    assert not any("prior chat" in t for t in all_texts)
    # Current turn synthesized at the tail
    last_texts = [c.text for c in msgs[-1].content if hasattr(c, "text")]
    assert any("isolated turn" == t for t in last_texts)
