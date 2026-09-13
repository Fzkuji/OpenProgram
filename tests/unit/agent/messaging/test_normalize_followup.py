"""``normalize_followup`` re-parents a followup reply onto a node that
SURVIVES filtering.

The old rule chained the reply to the sibling attach pointer — but
attach pointers are ``display=runtime`` rows that ``filter_visible``
strips, so the reply's predecessor pointed at a node absent from the
payload. The frontend edge walk dead-ended and the reply drew as an
orphan floating off every chain (two of those in the real session this
was written from). The rule now targets the followup user's own
predecessor: the turn that received the merge-back, which is a real
chain member.
"""
from openprogram.webui.graph_layout.filter import normalize_followup


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
    g = normalize_followup(_graph())
    reply = next(n for n in g if n["id"] == "fu_reply")
    assert reply["predecessor"] == "r1"


def test_missing_parent_leaves_reply_alone():
    g = _graph()
    fu = next(n for n in g if n["id"] == "fu")
    fu["predecessor"] = "gone"
    normalize_followup(g)
    reply = next(n for n in g if n["id"] == "fu_reply")
    assert reply["predecessor"] == "fu"


def test_non_followup_nodes_untouched():
    g = normalize_followup(_graph())
    r1 = next(n for n in g if n["id"] == "r1")
    assert r1["predecessor"] == "u1"
