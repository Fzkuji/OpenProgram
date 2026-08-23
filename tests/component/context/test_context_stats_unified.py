"""One context number, two readers.

The composer ring (WS ``context_stats``) and the ``/context`` panel
(``GET /api/sessions/{id}/context``) both take their headline total from
``openprogram.context.session_stats``, so they can never disagree.

Covered here:
- ``measured`` after a real request, ``estimated`` once the graph moves;
- the calibration ratio a measured reading leaves behind;
- ``/context`` returning the same ``total_used`` the ring broadcast;
- the four graph-change triggers each broadcasting a fresh estimate.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.context import session_stats as cs
from openprogram.webui import server as srv


class _FakeDB:
    """Two-message branch carrying recorded tools + system prompt."""

    def get_branch(self, session_id, head_id=None):
        return [
            {"role": "user", "content": "hello " * 200, "extra": None},
            {
                "role": "llm",
                "content": "hi " * 200,
                "extra": json.dumps({
                    "tools_available": ["bash", "read"],
                    "system_prompt": "You are a helpful agent. " * 20,
                }),
            },
        ]

    def get_session(self, session_id):
        return {"model": ""}


@pytest.fixture
def fake_db(monkeypatch):
    import openprogram.agent.session_db as _sdb
    monkeypatch.setattr(_sdb, "default_db", lambda: _FakeDB())
    return _FakeDB()


@pytest.fixture
def session(monkeypatch, fake_db):
    """A registered in-memory conversation plus a broadcast recorder."""
    sent: list[dict] = []
    monkeypatch.setattr(
        srv, "_broadcast_chat_response",
        lambda sid, mid, resp: sent.append(resp),
    )
    conv = {"provider_name": "anthropic", "head_id": None}
    srv._sessions["ctx-test"] = conv
    yield conv, sent
    srv._sessions.pop("ctx-test", None)


# --- basis selection -------------------------------------------------

def test_no_measurement_yields_estimated(fake_db):
    stats = cs.build_stats("s1")
    assert stats["basis"] == "estimated"
    assert stats["total_used"] == stats["estimated"] > 0
    assert "calibration" not in stats


def test_measurement_yields_measured_plus_calibration(fake_db):
    stats = cs.build_stats("s1", measured_total=12345, window=200_000)
    assert stats["basis"] == "measured"
    assert stats["total_used"] == 12345
    assert stats["window"] == 200_000
    # The estimate is still computed so the drift is reportable.
    assert stats["estimated"] > 0
    assert stats["calibration"] == pytest.approx(12345 / stats["estimated"], rel=1e-3)


def test_explicit_window_overrides_the_registry_lookup(fake_db):
    assert cs.build_stats("s1", window=1_000_000)["window"] == 1_000_000


# --- ring and panel agree --------------------------------------------

def test_panel_total_matches_the_ring_total(monkeypatch, session):
    conv, _sent = session
    srv.refresh_context_stats("ctx-test")
    ring_total = conv["_last_context_stats"]["total_used"]

    app = FastAPI()
    from openprogram.webui.routes import tree as _tree
    _tree.register(app)
    panel = TestClient(app).get("/api/sessions/ctx-test/context").json()

    assert panel["total_used"] == ring_total
    assert panel["window"] == conv["_last_context_stats"]["window"]
    assert panel["basis"] == "estimated"


def test_panel_reuses_the_measurement_while_the_graph_holds(session):
    conv, _sent = session
    conv["_last_context_stats"] = {
        "window": 200_000, "total_used": 77_777,
        "basis": "measured", "estimated": 60_000, "calibration": 1.3,
    }
    stats = srv.session_context_stats("ctx-test")
    assert stats["basis"] == "measured"
    assert stats["total_used"] == 77_777


def test_panel_drops_stale_breakdown_after_refresh_without_head_move(session):
    """Compact does not move HEAD. Refresh must still replace the
    pre-compact stored categories so /context does not keep 125k
    Messages next to a 40k rendered report."""
    conv, _sent = session
    conv["_last_context_stats"] = {
        "window": 200_000, "total_used": 137_000,
        "basis": "measured", "estimated": 60_000, "_context_rev": 0,
    }
    conv["_last_context_breakdown"] = {
        "messages": 125_400, "input_used": 137_000, "head_id": None,
        "system_prompt": 0, "tools_schema": 0, "tools_deferred_catalog": 0,
        "mcp_tools": 0, "mcp_tools_deferred": 0, "memory": 0, "skills": 0,
        "unclassified": 0, "window": 200_000, "_context_rev": 0,
    }
    srv.refresh_context_stats("ctx-test")

    app = FastAPI()
    from openprogram.webui.routes import tree as _tree
    _tree.register(app)
    panel = TestClient(app).get("/api/sessions/ctx-test/context").json()

    assert panel["basis"] == "estimated"
    assert panel["total_used"] != 137_000
    assert panel.get("messages", 0) != 125_400


def test_panel_re_estimates_for_a_different_branch(session):
    """A measurement belongs to the branch it was taken on, not to another."""
    conv, _sent = session
    conv["head_id"] = "head-a"
    conv["_last_context_stats"] = {
        "window": 200_000, "total_used": 77_777, "basis": "measured",
    }
    stats = srv.session_context_stats("ctx-test", head_id="head-b")
    assert stats["basis"] == "estimated"
    assert stats["total_used"] != 77_777


# --- graph-change refresh --------------------------------------------

def test_refresh_broadcasts_a_fresh_estimate(session):
    conv, sent = session
    conv["_last_context_stats"] = {
        "type": "context_stats", "window": 200_000,
        "total_used": 190_000, "basis": "measured", "calibration": 2.0,
    }
    srv.refresh_context_stats("ctx-test")

    assert len(sent) == 1
    out = sent[0]
    assert out["type"] == "context_stats"
    assert out["basis"] == "estimated"
    assert out["total_used"] < 190_000        # compaction-shaped drop
    # A stale calibration must not survive into an estimated reading.
    assert "calibration" not in out
    assert conv["_last_context_stats"] is out
    assert out.get("breakdown")
    assert out["breakdown"]["messages"] >= 0


def test_refresh_tracks_a_model_switch_window(monkeypatch, session):
    """Switching model changes the denominator without any request running."""
    conv, sent = session
    conv["_last_context_stats"] = {
        "basis": "measured", "total_used": 99_999,
        "window": 200_000, "calibration": 2.0,
    }
    monkeypatch.setattr(
        srv, "_resolve_context_window",
        lambda provider, model: 1_000_000 if model == "big" else 200_000,
    )
    conv["model_override"] = "big"
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["window"] == 1_000_000
    assert sent[-1]["context_window"] == 1_000_000
    assert sent[-1]["basis"] == "estimated"
    assert sent[-1]["total_used"] != 99_999
    assert "calibration" not in sent[-1]


def test_refresh_on_an_unknown_session_is_a_no_op(session):
    _conv, sent = session
    srv.refresh_context_stats("no-such-session")
    assert sent == []


def test_refresh_survives_an_estimator_failure(monkeypatch, session):
    """A transient DB error must not blank the ring."""
    conv, sent = session
    conv["_last_context_stats"] = {
        "window": 200_000, "total_used": 50_000, "basis": "measured",
    }
    monkeypatch.setattr(
        cs, "build_stats",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["total_used"] == 50_000


# --- the four triggers are wired ------------------------------------

def test_every_graph_change_calls_refresh(monkeypatch):
    """Compaction, model switch, checkout and delete all re-broadcast.

    Each site is grepped rather than driven end-to-end: the point is that
    no trigger is missing, and a dropped call would silently reintroduce
    the "compaction landed but the ring didn't move" bug.
    """
    import pathlib
    root = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps/server/openprogram_server/_webui"
    )
    wired = {
        "_execute/chat.py": "compaction",
        "ws_actions/chat.py": "manual /compact",
        "routes/runtime.py": "model switch (REST)",
        "ws_actions/runtime.py": "model switch (WS)",
        "ws_actions/branch.py": "branch checkout / delete",
        "_chat_routes.py": "sibling checkout",
    }
    for rel, what in wired.items():
        text = (root / rel).read_text(encoding="utf-8")
        assert "refresh_context_stats" in text, f"{what} lost its refresh"
    # branch.py holds two separate call sites (checkout and delete).
    assert (root / "ws_actions/branch.py").read_text(
        encoding="utf-8").count("refresh_context_stats") == 2
