"""A turn's standing and figures belong to its own branch.

Whether a turn carries the conversation's effects is frozen at admission,
never read from the current selection; the settings it runs with are
recorded with where each came from; and each branch keeps its own
occupancy, so two branches never overwrite each other's token count.
"""
from __future__ import annotations

import types

from openprogram.runtime.agent.turn_request import owns_conversation_effects
from openprogram.runtime.execution.foreground import foreground_task_snapshot
from openprogram.runtime.execution.model import ExecutionStatus

from tests.unit.agent.test_branch_agent_admission import two_branches
from tests.unit.dag.test_rewind_modes import store  # noqa: F401 - fixture


def _bind(monkeypatch, store):
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda sid, **kwargs: ("rules", kwargs.get("branch_id")))


# ── ownership ──────────────────────────────────────────────────────────


def test_ownership_is_frozen_at_admission(store, monkeypatch):
    a, b = two_branches(store)
    _bind(monkeypatch, store)
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    selected = freeze_branch_request({"session_id": "s"}, branch_authority=True)
    beside = freeze_branch_request({"session_id": "s", "branch_id": a, "side_branch": True},
                                   branch_authority=True)
    background = freeze_branch_request({"session_id": "s", "background": True})
    assert selected["owns_conversation_effects"] is True
    assert beside["owns_conversation_effects"] is False
    assert background["owns_conversation_effects"] is False
    assert owns_conversation_effects(beside) is False
    assert owns_conversation_effects(selected) is True


def test_a_request_admitted_before_the_flag_derives_it():
    assert owns_conversation_effects({"side_branch": True}) is False
    assert owns_conversation_effects({"background": True}) is False
    assert owns_conversation_effects({}) is True
    # The frozen flag wins over the old ones.
    assert owns_conversation_effects({"side_branch": True, "owns_conversation_effects": True}) is True


class _Store:
    def __init__(self, request):
        self.request = request

    def get_agent_turn_input(self, _execution_id):
        return {"kind": "chat", "request": {"source": "web", "interaction": None, **self.request}}

    def get_execution_input(self, _execution_id):
        return types.SimpleNamespace(user_message_id="m1")


def _running():
    return types.SimpleNamespace(
        execution_id="e1", session_id="s1", status=ExecutionStatus.RUNNING, status_version=1,
        reason_code=None, current_attempt_id="a1", created_at=1.0,
    )


def test_a_resumed_turn_keeps_its_standing_from_the_stored_request():
    # What a Worker restart reads back: the admitted request, flag included.
    beside = foreground_task_snapshot(_Store({"branch_id": "x", "owns_conversation_effects": False}), _running())
    owner = foreground_task_snapshot(_Store({"branch_id": "y", "owns_conversation_effects": True,
                                             "side_branch": True}), _running())
    legacy = foreground_task_snapshot(_Store({"branch_id": "z", "side_branch": True}), _running())
    assert beside["side_branch"] is True
    assert owner["side_branch"] is False
    assert legacy["side_branch"] is True


# ── turn settings record ───────────────────────────────────────────────


def test_admission_records_each_setting_with_its_source(store, monkeypatch):
    a, _b = two_branches(store)
    _bind(monkeypatch, store)
    from openprogram.runtime.conversation.branch_config import freeze_branch_request
    frozen = freeze_branch_request({"session_id": "s", "branch_id": a, "thinking_effort": "max",
                                    "agent_id": None, "permission_mode": None})
    record = frozen["turn_settings"]
    assert record["agent_id"] == "alpha"
    assert record["values"]["thinking_effort"] == "max"
    assert record["sources"]["thinking_effort"] == "request"
    assert (record["values"]["permission_mode"], record["sources"]["permission_mode"]) == ("bypass", "branch")
    assert record["sources"]["agent_id"] == "branch"
    # No model chosen on the branch: the Agent decides when the turn runs.
    assert record["values"]["model_override"] is None
    assert record["sources"]["model_override"] == "agent"
    # A resumed turn keeps what it was admitted with.
    store.update_branch_config("s", a, {"thinking_effort": "low"})
    again = freeze_branch_request(dict(frozen))
    assert again["turn_settings"] == record


# ── per-branch occupancy ───────────────────────────────────────────────


def test_a_branch_record_keeps_its_own_token_count(store):
    a, b = two_branches(store)
    store.update_session("s", last_prompt_tokens=900)
    assert store.update_branch_fields("s", a, last_prompt_tokens=120)
    assert store.update_branch_fields("s", b, last_prompt_tokens=900)
    row = store.get_session("s")
    assert row["branch_refs"][a]["last_prompt_tokens"] == 120
    assert row["branch_refs"][b]["last_prompt_tokens"] == 900
    # The conversation's figure is still the selected branch's.
    assert row["last_prompt_tokens"] == 900
    assert store.update_branch_fields("s", "branch_missing", last_prompt_tokens=1) is False


def test_two_branches_budget_against_their_own_last_request(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    from openprogram.runtime.context.usage import UsageTracker
    tracker = UsageTracker()
    tracker.record_turn("s", usage={"context_tokens": 50_000, "input_tokens": 50_000}, branch_id=b)
    tracker.record_turn("s", usage={"context_tokens": 7_000, "input_tokens": 7_000}, branch_id=a)
    assert tracker.get("s", b).last_prompt_tokens == 50_000
    assert tracker.get("s", a).last_prompt_tokens == 7_000
    # The projection (what the ring reads) is the selected branch's; the
    # billing totals add up across branches.
    assert tracker.get("s").last_prompt_tokens == 50_000
    assert tracker.get("s").cumulative_prompt_tokens == 57_000
    # A branch with no request of its own reads none of another's.
    assert tracker.get("s", "branch_new").last_prompt_tokens == 0
    # The figures survive a restart.
    reloaded = UsageTracker()
    assert reloaded.get("s", a).last_prompt_tokens == 7_000
    assert reloaded.get("s", b).last_prompt_tokens == 50_000
    assert reloaded.get("s").last_prompt_tokens == 50_000


def test_the_turns_branch_comes_from_the_usage_context(store, monkeypatch):
    a, b = two_branches(store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    from dataclasses import replace
    from openprogram.models.usage.context import _current
    from openprogram.runtime.context.usage import UsageTracker
    tracker = UsageTracker()
    tracker.record_turn("s", usage={"context_tokens": 40_000}, branch_id=b)
    token = _current.set(replace(_current.get(), session_id="s", branch_id=a))
    try:
        tracker.record_turn("s", usage={"context_tokens": 3_000})
        assert tracker.get("s").last_prompt_tokens == 3_000
    finally:
        _current.reset(token)
    assert tracker.get("s").last_prompt_tokens == 40_000
