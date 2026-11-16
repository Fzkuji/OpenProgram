"""One context number, two readers.

The composer ring (WS ``context_stats``) and the ``/context`` panel
(``GET /api/sessions/{id}/context``) both take their headline total from
``openprogram.runtime.context.token_accounting.session_stats``, so they can never disagree.

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

from openprogram.runtime.context.token_accounting import session_stats as cs
from openprogram.interfaces.server import server as srv


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
    import openprogram.runtime.context.session_db as _sdb
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


def test_calibration_pairs_provider_usage_with_dispatched_request(fake_db):
    measured = cs.build_stats(
        "s1", measured_total=120_000, request_input_estimate=60_000,
    )
    assert measured["calibration"] == 2.0
    assert measured["calibration_source"] == "request"
    after_compaction = cs.build_stats("s1", estimated_total=30_000, calibration=2.0)
    assert after_compaction["basis"] == "estimated"
    assert after_compaction["total_used"] == 60_000


def test_refresh_reuses_only_same_model_request_calibration(monkeypatch, session):
    conv, sent = session
    conv["runtime"] = type("Runtime", (), {"model": "m1"})()
    conv["_last_context_stats"] = {
        "basis": "measured", "total_used": 120_000,
        "calibration": 2.0, "calibration_source": "request",
        "context_usage_version": 2,
        "provider": "anthropic", "model": "m1", "window": 200_000,
    }
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["total_used"] == 2 * sent[-1]["estimated"]
    assert srv.session_context_stats("ctx-test")["total_used"] == sent[-1]["total_used"]
    conv["model_override"] = "m2"
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["total_used"] == sent[-1]["estimated"]


def test_websocket_usage_carries_paired_request_estimate(monkeypatch, session):
    from openprogram.interfaces.server.ws_actions.chat import _record_context_usage
    conv, sent = session
    conv["runtime"] = type("Runtime", (), {"model": "m1"})()
    monkeypatch.setattr(srv, "_save_session", lambda _sid: None)
    _record_context_usage("ctx-test", "msg", conv, {
        "input_tokens": 120_000, "output_tokens": 10,
        "request_input_estimate": 60_000,
    })
    assert sent[-1]["basis"] == "measured"
    assert sent[-1]["total_used"] == 120_000
    assert sent[-1]["calibration"] == 2.0
    assert sent[-1]["calibration_source"] == "request"


def test_explicit_window_overrides_the_registry_lookup(fake_db):
    assert cs.build_stats("s1", window=1_000_000)["window"] == 1_000_000


# --- ring and panel agree --------------------------------------------

def test_panel_total_matches_the_ring_total(monkeypatch, session):
    conv, _sent = session
    srv.refresh_context_stats("ctx-test")
    ring_total = conv["_last_context_stats"]["total_used"]

    app = FastAPI()
    from openprogram.interfaces.server.routes.files import tree as _tree
    _tree.register(app)
    panel = TestClient(app).get("/api/sessions/ctx-test/context").json()

    assert panel["total_used"] == ring_total
    assert panel["window"] == conv["_last_context_stats"]["window"]
    assert panel["basis"] == "estimated"


def test_panel_reuses_the_measurement_while_the_graph_holds(session):
    conv, _sent = session
    conv["_last_context_stats"] = {
        "context_usage_version": 2,
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
    from openprogram.interfaces.server.routes.files import tree as _tree
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
        "context_usage_version": 2,
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


def test_refresh_recovers_provider_usage_newer_than_stale_badge(monkeypatch, session):
    conv, sent = session
    conv["_last_context_stats"] = {
        "basis": "estimated", "total_used": 38_000,
        "window": 1_000_000, "timestamp": 100.0,
    }
    import openprogram.runtime.context.session_db as session_db
    monkeypatch.setattr(session_db, "default_db", lambda: type("DB", (), {
        "get_session": lambda self, _sid: {
            "updated_at": 150.0,
            "_usage": json.dumps({"context_usage_version": 2, "last_updated_at": 200.0,
                                  "last_prompt_tokens": 99_000,
                                  "last_cache_read_tokens": 3_000}),
        },
        "get_branch": lambda self, _sid, head_id=None: _FakeDB().get_branch(_sid),
    })())
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["basis"] == "measured"
    assert sent[-1]["total_used"] == 102_000


def test_refresh_does_not_reuse_usage_after_later_branch_change(monkeypatch, session):
    conv, sent = session
    conv["_last_context_stats"] = {
        "basis": "estimated", "total_used": 38_000,
        "window": 1_000_000, "timestamp": 100.0,
    }
    import openprogram.runtime.context.session_db as session_db
    monkeypatch.setattr(session_db, "default_db", lambda: type("DB", (), {
        "get_session": lambda self, _sid: {
            "updated_at": 250.0,
            "_usage": {"last_updated_at": 200.0,
                       "last_prompt_tokens": 99_000},
        },
        "get_branch": lambda self, _sid, head_id=None: _FakeDB().get_branch(_sid),
    })())
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["basis"] == "estimated"


def test_refresh_on_an_unknown_session_is_a_no_op(session):
    _conv, sent = session
    srv.refresh_context_stats("no-such-session")
    assert sent == []


def test_refresh_survives_an_estimator_failure(monkeypatch, session):
    """A transient DB error must not blank the ring."""
    conv, sent = session
    conv["_last_context_stats"] = {
        "context_usage_version": 2,
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
        / "openprogram/interfaces/server"
    )
    wired = {
        "ws_actions/chat/context_usage.py": "in-flight context usage",
        "ws_actions/chat/commands.py": "manual /compact",
        "routes/execution/runtime.py": "model switch (REST)",
        "ws_actions/runtime.py": "model switch (WS)",
        "ws_actions/branch.py": "branch checkout / delete",
        "routes/chat_branches.py": "sibling checkout",
    }
    for rel, what in wired.items():
        text = (root / rel).read_text(encoding="utf-8")
        assert "refresh_context_stats" in text, f"{what} lost its refresh"
    # branch.py holds two separate call sites (checkout and delete).
    assert (root / "ws_actions/branch.py").read_text(
        encoding="utf-8").count("refresh_context_stats") == 2


def test_missing_last_request_does_not_display_turn_billing(monkeypatch, session):
    from openprogram.interfaces.server.ws_actions.chat import _record_context_usage
    conv, sent = session
    conv["_last_context_stats"] = {
        "total_used": 2404285, "basis": "measured",
        "calibration": 24.6101, "calibration_source": "request",
    }
    _record_context_usage("ctx-test", "msg", conv, {
        "input_tokens": 2254931, "cache_read_tokens": 149354,
        "context_tokens": 0, "request_input_estimate": 97695,
    })
    assert sent[-1]["basis"] == "estimated"
    assert sent[-1]["total_used"] == sent[-1]["estimated"]
    assert sent[-1]["total_used"] < 100000
    assert "calibration" not in sent[-1]


def test_usage_tracker_separates_billing_from_occupancy():
    from openprogram.runtime.context.token_accounting.usage import UsageTracker
    from openprogram.runtime.context.types import UsageState
    tracker = UsageTracker()
    tracker._cache["s"] = UsageState()
    state = tracker.record_turn("s", usage={
        "input_tokens": 2254931, "cache_read_tokens": 149354,
        "context_tokens": 100000,
    }, persist=False)
    assert state.last_prompt_tokens == 100000
    assert state.last_cache_read_tokens == 0
    assert state.cumulative_prompt_tokens == 2254931
    assert state.cumulative_cache_read_tokens == 149354
    state = tracker.record_turn("s", usage={
        "input_tokens": 2000000, "context_tokens": 0,
    }, persist=False)
    assert state.last_prompt_tokens == 0
    assert state.source == "estimate"


# --- the window is the model's real one ------------------------------

_REAL = {("openai-codex", "gpt-6.1-sol"): 500_000}


@pytest.fixture
def registry(monkeypatch):
    """A catalog holding one model whose window is not the 128k default."""
    from types import SimpleNamespace
    import openprogram.models.providers.catalog.models as catalog
    monkeypatch.setattr(catalog, "get_model", lambda provider, model_id: (
        SimpleNamespace(provider=provider, id=model_id,
                        context_window=_REAL[(provider, model_id)])
        if (provider, model_id) in _REAL else None))
    return catalog


def _real_window():
    from openprogram.models.providers.catalog import models as catalog
    from openprogram.runtime.context.token_accounting.tokens import real_context_window
    return real_context_window(catalog.get_model("openai-codex", "gpt-6.1-sol"))


@pytest.mark.parametrize("stored", ["openai-codex/gpt-6.1-sol", "openai-codex:gpt-6.1-sol"])
def test_a_session_row_model_gets_the_window_compaction_reads(monkeypatch, registry, stored):
    """Sessions store ``provider/model``; both spellings name the provider."""
    import openprogram.runtime.context.session_db as _sdb

    class _Row(_FakeDB):
        def get_session(self, session_id):
            return {"model": stored}

    monkeypatch.setattr(_sdb, "default_db", lambda: _Row())
    bd = cs.compute_breakdown("s1")
    assert bd["context_window"] == _real_window() == 500_000
    assert bd["model"] == "gpt-6.1-sol"
    assert cs.build_stats("s1")["window"] == 500_000


def test_an_unresolvable_model_has_no_window(monkeypatch, registry):
    import openprogram.runtime.context.session_db as _sdb

    class _Row(_FakeDB):
        def get_session(self, session_id):
            return {"model": "nowhere/unknown-model"}

    monkeypatch.setattr(_sdb, "default_db", lambda: _Row())
    stats = cs.build_stats("s1", measured_total=50_000)
    assert stats["window"] is None
    final = cs.finalize_breakdown(stats.pop("_breakdown"), stats)
    assert final["window"] is None and final["free_space"] is None


def test_the_turn_report_carries_its_model_and_window(registry):
    """The in-flight report names the Model the turn resolved."""
    from types import SimpleNamespace
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    frames = []
    msg = SimpleNamespace(role="assistant", content=[], stop_reason="stop",
                          usage=SimpleNamespace(input=120_000, output=10, cache_read=0,
                                                cache_write=0))
    model = registry.get_model("openai-codex", "gpt-6.1-sol")
    loop_runner._record_turn_end(
        SimpleNamespace(type="turn_end", message=msg), loop_runner._TurnTally(),
        req=SimpleNamespace(session_id="s", user_msg_id="u"),
        on_event=frames.append, ordered_blocks_out=None, model=model,
    )
    report = frames[-1]["data"]
    assert report["type"] == "context_usage"
    assert report["model"] == "openai-codex/gpt-6.1-sol"
    assert report["context_window"] == _real_window()


def test_a_chat_following_its_agent_broadcasts_the_turn_model_window(monkeypatch, registry, session):
    """No pin and no runtime model: the turn's own model decides."""
    from openprogram.interfaces.server.ws_actions.chat import (
        _record_canonical_context_stats, _record_context_usage,
    )
    conv, sent = session
    monkeypatch.setattr(srv, "_save_session", lambda _sid: None)
    monkeypatch.setattr(srv, "_hydrate_messages_from_db", lambda _sid: None)
    _record_context_usage("ctx-test", "msg", conv, {"input_tokens": 134_300},
                          model="openai-codex/gpt-6.1-sol", context_window=_real_window())
    assert sent[-1]["window"] == sent[-1]["context_window"] == 500_000
    assert (sent[-1]["provider"], sent[-1]["model"]) == ("openai-codex", "gpt-6.1-sol")
    # The finished turn's report carries no model; it keeps the turn's.
    _record_canonical_context_stats("ctx-test", "msg", conv, type("R", (), {
        "usage": {"input_tokens": 134_400, "context_tokens": 134_400}})())
    assert sent[-1]["window"] == 500_000
    assert srv.session_context_stats("ctx-test")["window"] == 500_000


def test_a_chat_following_its_agent_reads_the_agent_model_after_reload(monkeypatch, registry, session):
    from openprogram.runtime.conversation.session_settings import session_model
    conv, sent = session
    conv.update({"id": "ctx-test", "agent_id": "main"})
    monkeypatch.setattr(session_model, "new_session_chat_model",
                        lambda _agent=None: ("openai-codex", "gpt-6.1-sol"))
    srv.refresh_context_stats("ctx-test")
    assert sent[-1]["window"] == 500_000


def test_a_stored_128k_window_is_replaced_by_the_real_one(monkeypatch, registry, session):
    """Records saved while the lookup failed carry 128k; it must not stick."""
    from openprogram.interfaces.server.ws_actions.session.load import _context_stats_for_load
    conv, _sent = session
    conv["model_override"] = "gpt-6.1-sol"
    conv["provider_override"] = "openai-codex"
    stale = {"messages": 100_000, "input_used": 134_300, "head_id": None,
             "system_prompt": 0, "tools_schema": 0, "tools_deferred_catalog": 0,
             "mcp_tools": 0, "mcp_tools_deferred": 0, "memory": 0, "skills": 0,
             "unclassified": 0, "context_window": 128_000, "window": 128_000,
             "free_space": 0, "_context_rev": 0}
    conv["_last_context_stats"] = {
        "context_usage_version": 2, "type": "context_stats", "window": 128_000,
        "context_window": 128_000, "total_used": 134_300, "basis": "measured",
        "estimated": 120_000, "_context_rev": 0,
    }
    conv["_last_context_breakdown"] = dict(stale)

    assert srv.session_context_stats("ctx-test")["window"] == 500_000

    app = FastAPI()
    from openprogram.interfaces.server.routes.files import tree as _tree
    _tree.register(app)
    panel = TestClient(app).get("/api/sessions/ctx-test/context").json()
    assert panel["window"] == panel["context_window"] == 500_000
    assert panel["free_space"] == 500_000 - 134_300

    stats, _refresh = _context_stats_for_load(conv)
    assert stats["window"] == stats["context_window"] == 500_000
    assert stats["breakdown"]["window"] == 500_000


def test_a_running_turn_counts_its_growth_as_conversation():
    breakdown = {"system_prompt": 100, "messages": 200, "tools_schema": 100,
                 "input_used": 400, "messages_detail": {
                     "groups": [{"kind": "user", "tokens": 200, "count": 2}], "top": []}}
    occupancy = {"window": 2_000, "total_used": 1_000, "basis": "measured"}
    done = cs.finalize_breakdown(breakdown, occupancy)
    assert done["unclassified"] == 600
    # The request's own estimate (900) holds 500 more than the graph (400):
    # that is the turn's growth; the other 100 stays unclassified.
    running = cs.finalize_breakdown(breakdown, occupancy, running_turn=True,
                                    request_estimate=900)
    assert running["unclassified"] == 100
    assert running["messages"] == 700
    assert running["messages_detail"]["groups"][0] == {"kind": "turn", "tokens": 500, "count": 0}


def test_a_running_turns_first_request_leaves_provider_overhead_unclassified():
    """No growth yet (the request estimate equals the graph's): the
    residual is the provider's own prompt, not this turn."""
    breakdown = {"system_prompt": 100, "messages": 200, "tools_schema": 100,
                 "input_used": 400, "messages_detail": {
                     "groups": [{"kind": "user", "tokens": 200, "count": 2}], "top": []}}
    occupancy = {"window": 2_000, "total_used": 1_000, "basis": "measured"}
    for estimate in (None, 0, 400):
        running = cs.finalize_breakdown(breakdown, occupancy, running_turn=True,
                                        request_estimate=estimate)
        assert running["unclassified"] == 600
        assert running["messages"] == 200
        assert all(g["kind"] != "turn" for g in running["messages_detail"]["groups"])


def test_a_bare_id_holding_a_separator_stays_whole_under_its_provider(monkeypatch):
    from openprogram.models.providers.catalog import models as catalog
    registered = {("ollama", "llama3:8b"), ("openrouter", "meta-llama/llama-3:free")}
    monkeypatch.setattr(catalog, "get_model",
                        lambda p, m: object() if (p, m) in registered else None)
    for provider, model_id in registered:
        assert catalog.split_model_ref(model_id, provider) == (provider, model_id)
        assert catalog.split_model_ref(f"{provider}/{model_id}") == (provider, model_id)
        assert catalog.split_model_ref(f"{provider}/{model_id}", "other") == (provider, model_id)
    # An id the provider does not register still splits as a reference.
    assert catalog.split_model_ref("openai-codex/gpt-6.1-sol", "ollama") == (
        "openai-codex", "gpt-6.1-sol")


# --- a branch's window is its own model's ----------------------------

_REAL_BIG = ("openai-codex", "gpt-6.1-terra")


class _BranchedDB(_FakeDB):
    """``head-a`` is selected and follows the Agent; ``head-b`` ends a
    branch that keeps its own model in ``branch_config``."""

    def get_session(self, session_id):
        return {
            "model": "", "head_id": "head-a", "active_branch_id": "branch-a",
            "branch_refs": {
                "branch-a": {"branch_id": "branch-a", "head_id": "head-a", "config": {}},
                "branch-b": {"branch_id": "branch-b", "head_id": "head-b", "config": {
                    "provider_override": _REAL_BIG[0], "model_override": _REAL_BIG[1]}},
                "branch-gone": {"branch_id": "branch-gone", "head_id": "head-c",
                                "status": "aborted", "config": {
                                    "provider_override": _REAL_BIG[0],
                                    "model_override": _REAL_BIG[1]}},
            },
        }


@pytest.fixture
def branched(monkeypatch, registry, session):
    import openprogram.runtime.context.session_db as _sdb
    monkeypatch.setitem(_REAL, _REAL_BIG, 1_000_000)
    monkeypatch.setattr(_sdb, "default_db", lambda: _BranchedDB())
    conv, sent = session
    conv.update({"id": "ctx-test", "head_id": "head-a",
                 "provider_override": "openai-codex", "model_override": "gpt-6.1-sol"})
    return conv, sent


def test_a_branch_with_its_own_model_shows_that_models_window(branched):
    """Read at a branch that is not selected: its model, not the selected one's."""
    conv, _sent = branched
    assert srv._conv_context_window(conv) == 500_000
    assert srv._conv_context_window(conv, "head-a") == 500_000
    assert srv._conv_context_window(conv, "head-b") == 1_000_000
    assert srv._conv_model(conv, "head-b") == _REAL_BIG
    assert srv.session_context_stats("ctx-test", head_id="head-b")["window"] == 1_000_000
    assert srv.session_context_stats("ctx-test")["window"] == 500_000

    app = FastAPI()
    from openprogram.interfaces.server.routes.files import tree as _tree
    _tree.register(app)
    client = TestClient(app)
    panel = client.get("/api/sessions/ctx-test/context", params={"head_id": "head-b"}).json()
    assert panel["window"] == panel["context_window"] == 1_000_000
    assert panel["model"] == "gpt-6.1-terra"
    selected = client.get("/api/sessions/ctx-test/context", params={"head_id": "head-a"}).json()
    assert selected["window"] == 500_000


def test_a_head_no_live_branch_ends_at_reads_the_selected_model(branched):
    conv, _sent = branched
    # An aborted record and a tip no branch is recorded at say nothing.
    assert srv._conv_context_window(conv, "head-c") == 500_000
    assert srv._conv_context_window(conv, "head-unknown") == 500_000


def test_an_unselected_branch_without_a_model_follows_its_agent(monkeypatch, branched):
    """The selected branch's pin and last turn say nothing about it."""
    from openprogram.runtime.conversation.session_settings import session_model
    conv, _sent = branched
    conv["_turn_model_ref"] = "openai-codex/gpt-6.1-sol"

    class _AgentBranch(_BranchedDB):
        def get_session(self, session_id):
            row = super().get_session(session_id)
            row["branch_refs"]["branch-b"]["config"] = {"agent_id": "writer"}
            return row

    import openprogram.runtime.context.session_db as _sdb
    monkeypatch.setattr(_sdb, "default_db", lambda: _AgentBranch())
    asked = []
    monkeypatch.setattr(session_model, "new_session_chat_model",
                        lambda agent=None: asked.append(agent) or _REAL_BIG)
    assert srv._conv_context_window(conv, "head-b") == 1_000_000
    assert asked == ["writer"]


def test_a_branch_switch_drops_the_left_branchs_turn_model(monkeypatch, tmp_path):
    """The branch switched to sets no model: the turn that ran on the
    branch that was left must not decide its window."""
    import threading
    from openprogram.state.store import SessionNodeWriter, SessionStore
    from openprogram.state.store.session.nodes import ROLE_USER, Call

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    try:
        sid = "ctx-switch"
        store.create_session(sid, "main")
        writer = SessionNodeWriter(store, sid)
        writer.append(Call(id="ROOT", role=ROLE_USER, output="", metadata={"display": "root"}))
        writer.append(Call(id="u1", role=ROLE_USER, output="one", caller="ROOT", predecessor="ROOT"))
        store.set_head(sid, "u1")
        writer.append(Call(id="u2", role=ROLE_USER, output="two", caller="ROOT", predecessor="u1"))
        store.set_head(sid, "u2")
        conv = {"id": sid, "messages": [], "head_id": "u2",
                "_turn_model_ref": "openai-codex/gpt-6.1-sol", "_turn_context_window": 500_000}
        monkeypatch.setattr(srv, "_sessions", {sid: conv})
        monkeypatch.setattr(srv, "_sessions_lock", threading.Lock())
        monkeypatch.setattr(srv, "_invalidate_messages", lambda _sid: None)

        assert srv._set_active_head(sid, "u1")
        assert conv["head_id"] == "u1"
        assert "_turn_model_ref" not in conv
        assert "_turn_context_window" not in conv
    finally:
        store.close()


def test_the_breakdown_states_the_compaction_in_effect_on_the_branch():
    branch = [
        {"id": "summary_1", "role": "llm", "compaction": True, "covers_ids": ["a", "b", "c"],
         "summarised_count": 25, "tokens_before": 354_455, "tokens_after": 29_975,
         "compacted_at": 1_700_000_000.0},
        {"id": "u9", "role": "user", "content": "next"},
    ]
    assert cs._last_compaction(branch) == {
        "covered": 25, "tokens_before": 354_455, "tokens_after": 29_975, "at": 1_700_000_000.0}
    # An old summary without the stamped facts still says how much it covers.
    assert cs._last_compaction([{"id": "s", "compaction": True, "covers_ids": ["a", "b"]}]) == {"covered": 2}
    assert cs._last_compaction(branch[1:]) is None
