"""Branch rows mark a sub-agent's conversation kept in the parent session."""
from __future__ import annotations

from types import SimpleNamespace

import openprogram.runtime.context.session_db as session_db
import openprogram.state.store as store
from openprogram.interfaces.server.ws_actions import branch


def _node(node_id, predecessor=None, **metadata):
    return SimpleNamespace(id=node_id, predecessor=predecessor, metadata=metadata)


def test_a_tip_that_starts_at_a_spawn_root_is_a_sub_agent(monkeypatch):
    nodes = {n.id: n for n in [
        _node("u1"), _node("r1", "u1"),
        # An older session keeps the sub-agent's turns inline: its user
        # node is a spawn root and its reply has no caller.
        _node("j_a_user", spawn_branch_root=True, source="agent_spawn"),
        _node("j_a_user_reply", "j_a_user", source="agent_spawn"),
        _node("j_b_user", source="agent_spawn"), _node("j_b_user_reply", "j_b_user"), _node("j_b_more", "j_b_user_reply"),
    ]}

    class Writer:
        def __init__(self, db, sid):
            pass

        def load(self):
            return SimpleNamespace(nodes=nodes)

    monkeypatch.setattr(store, "SessionNodeWriter", Writer)
    monkeypatch.setattr(session_db, "default_db", lambda: None)
    heads = ["r1", "j_a_user_reply", "j_b_more", "missing"]
    assert branch._spawned_heads("s", heads) == {"j_a_user_reply", "j_b_more"}
