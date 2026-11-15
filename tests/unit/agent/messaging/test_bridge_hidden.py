"""``bridge_hidden`` re-parents a node whose predecessor is filtered onto
a node that SURVIVES filtering.

The old rule chained the reply to the sibling attach pointer — but
attach pointers are ``display=runtime`` rows that ``filter_visible``
strips, so the reply's predecessor pointed at a node absent from the
payload. The frontend edge walk dead-ended and the reply drew as an
orphan floating off every chain (two of those in the real session this
was written from). The rule now targets the followup user's own
predecessor: the turn that received the merge-back, which is a real
chain member.
"""
from openprogram.state.store.session.graph.layout.filter import filter_visible, bridge_hidden


def _graph():
    return [
        {"id": "u1", "role": "user", "predecessor": "ROOT"},
        {"id": "r1", "role": "llm", "predecessor": "u1"},
        # attach pointer: display=runtime, filtered later — never a
        # legal predecessor target.
        {"id": "atc", "role": "llm", "function": "attach",
         "display": "runtime", "predecessor": "r1", "caller": "r1"},
        # synthetic followup trigger (filtered later too)
        {"id": "fu", "role": "user", "source": "job_followup",
         "predecessor": "r1"},
        {"id": "fu_reply", "role": "assistant", "source": "job_followup",
         "predecessor": "fu"},
    ]


def test_reply_reparents_to_merge_back_turn_not_attach():
    g = bridge_hidden(_graph())
    reply = next(n for n in g if n["id"] == "fu_reply")
    assert reply["predecessor"] == "r1"


def test_missing_parent_leaves_reply_alone():
    g = _graph()
    fu = next(n for n in g if n["id"] == "fu")
    fu["predecessor"] = "gone"
    bridge_hidden(g)
    reply = next(n for n in g if n["id"] == "fu_reply")
    assert reply["predecessor"] == "fu"


def test_non_followup_nodes_untouched():
    g = bridge_hidden(_graph())
    r1 = next(n for n in g if n["id"] == "r1")
    assert r1["predecessor"] == "u1"


def test_background_command_notice_reply_joins_the_chain():
    # A background command ending writes a display=runtime notice and the
    # agent's reply; the next user message chains to that reply. With the
    # notice stripped, the reply must still point at a painted node.
    g = [
        {"id": "u1", "role": "user", "predecessor": "ROOT"},
        {"id": "r1", "role": "assistant", "predecessor": "u1"},
        {"id": "px", "role": "user", "source": "process_exit",
         "display": "runtime", "predecessor": "r1"},
        {"id": "px_reply", "role": "assistant", "source": "process_exit",
         "predecessor": "px"},
        {"id": "u2", "role": "user", "predecessor": "px_reply"},
    ]
    visible = filter_visible(bridge_hidden(g))
    ids = {n["id"] for n in visible}
    assert "px" not in ids
    assert all(n["predecessor"] in ids | {"ROOT"} for n in visible)
    assert next(n for n in visible if n["id"] == "px_reply")["predecessor"] == "r1"


def test_restart_turn_keeps_the_resumed_branch_attached():
    # Restarting an interrupted run writes a display=runtime "continue the
    # interrupted task" user turn (source=web, not a runtime-notice
    # source). Stripping it used to leave the resumed reply pointing at
    # nothing, so the reply and every turn after it drew as a branch
    # floating off the graph.
    g = [
        {"id": "ROOT", "role": "user", "display": "root", "predecessor": ""},
        {"id": "u1", "role": "user", "predecessor": "ROOT"},
        {"id": "u1_reply", "role": "assistant", "predecessor": "u1"},
        {"id": "x_user", "role": "user", "source": "web",
         "display": "runtime", "trigger": "restart",
         "predecessor": "u1_reply"},
        {"id": "x_user_reply", "role": "assistant", "source": "web",
         "predecessor": "x_user"},
        {"id": "u2", "role": "user", "predecessor": "x_user_reply"},
    ]
    visible = filter_visible(bridge_hidden(g))
    ids = {n["id"] for n in visible}
    assert "x_user" not in ids
    assert all(n["predecessor"] in ids for n in visible if n["id"] != "ROOT")
    resumed = next(n for n in visible if n["id"] == "x_user_reply")
    assert resumed["predecessor"] == "u1_reply"
    # The rest of the cut-off reply: the frontend merges it into u1_reply.
    assert resumed["resumed"] is True
    assert "resumed" not in next(n for n in visible if n["id"] == "u2")


def test_notice_reply_is_not_marked_resumed():
    g = bridge_hidden(_graph())
    assert "resumed" not in next(n for n in g if n["id"] == "fu_reply")


def test_chained_hidden_rows_bridge_to_the_first_visible_turn():
    g = [
        {"id": "u1", "role": "user", "predecessor": "ROOT"},
        {"id": "atc", "role": "llm", "function": "attach",
         "display": "runtime", "predecessor": "u1"},
        {"id": "x_user", "role": "user", "display": "runtime",
         "predecessor": "atc"},
        {"id": "x_reply", "role": "assistant", "predecessor": "x_user"},
    ]
    bridge_hidden(g)
    assert next(n for n in g if n["id"] == "x_reply")["predecessor"] == "u1"
