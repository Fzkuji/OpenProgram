from types import SimpleNamespace

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_chat_goal.runtime


def test_terminal_record_survives_new_goal_and_repeated_save(runtime):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    first = goals.load_goal("goal-chat")
    first["text"] = "First objective"
    assert not first.get("end_records")
    first.update(status="achieved", last_reason="Verified")
    goals.save_goal("goal-chat", first)
    saved = goals.load_goal("goal-chat")
    assert len(saved["end_records"]) == 1
    record = saved["end_records"][0]
    assert record["goal"]["text"] == "First objective"
    assert "end_records" not in record["goal"]
    goals.save_goal("goal-chat", saved)
    assert len(goals.load_goal("goal-chat")["end_records"]) == 1
    second = chat.create("goal-chat", "Second objective")
    assert second["end_records"][0] == record
    assert second["goal_id"] != record["goal"]["goal_id"]


def test_cancelled_record_uses_public_head_and_stays_after_reload(runtime):
    import openprogram.programs.tools.planning.goal as goals
    db = goals._db()
    db.append_message("goal-chat", {"id": "at-end", "role": "assistant", "content": "Done work", "timestamp": 10})
    goal = goals.load_goal("goal-chat")
    goal.update(status="cancelled", verification={"result_message_id": "old-failed-check"})
    goals.save_goal("goal-chat", goal)
    assert goals.load_goal("goal-chat")["end_records"][0]["anchor_id"] == "at-end"
    db.append_message("goal-chat", {"id": "later", "role": "user", "content": "Next", "predecessor": "at-end", "timestamp": 20})
    db.invalidate_cache("goal-chat")
    assert goals.load_goal("goal-chat")["end_records"][0]["anchor_id"] == "at-end"


def test_legacy_terminal_anchor_is_based_on_ending_time_not_current_head(runtime):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal.presentation import history_projection
    db = goals._db()
    db.append_message("goal-chat", {"id": "original", "role": "assistant", "content": "Work", "timestamp": 10})
    old = {"goal_id": "legacy", "text": "Original", "status": "cancelled", "updated_at": 15}
    db.update_session("goal-chat", goal=old)
    assert history_projection(old, "goal-chat")["end_records"][0]["anchor_id"] == "original"
    db.append_message("goal-chat", {"id": "newer", "role": "user", "content": "Next", "predecessor": "original", "timestamp": 20})
    db.invalidate_cache("goal-chat")
    assert goals.load_goal("goal-chat")["end_records"][0]["anchor_id"] == "original"


def test_event_identity_is_exact_and_does_not_rewrite_content():
    from openprogram.programs.tools.planning.goal.presentation import event_sink
    output = []
    req = SimpleNamespace(goal_verification="candidate", session_id="s", user_msg_id="u")
    emit = event_sink(output.append, req)
    raw = {"type": "chat_response", "data": {"type": "stream_event", "session_id": "s", "msg_id": "u", "event": {"type": "text", "text": '{"requirements":'}}}
    emit(raw)
    assert output[-1]["data"]["goal_verification"] == {"id": "candidate", "status": "pending"}
    assert output[-1]["data"]["event"] == raw["data"]["event"]
    assert "goal_verification" not in raw["data"]
    for sid, mid, kind in [("other", "u", "stream_event"), ("s", "other", "stream_event"), ("s", "u", "user_message")]:
        emit({"type": "chat_response", "data": {"type": kind, "session_id": sid, "msg_id": mid}})
        assert "goal_verification" not in output[-1]["data"]


def test_history_requires_canonical_identity_not_json_shape(runtime):
    from openprogram.programs.tools.planning.goal.presentation import annotate_messages
    rows = [{"id": "ordinary", "role": "assistant", "content": '{"requirements":[{"verdict":"met"}]}'}]
    assert annotate_messages("goal-chat", rows) == rows


def test_malformed_report_stays_unmet_and_display_safe():
    from openprogram.programs.tools.planning.goal.presentation import candidate_presentation
    for report in [[], "bad", {"requirements": "bad"}, {"requirements": [None, "bad"]}]:
        projected = candidate_presentation({"id": "c", "status": "unmet", "report": report})
        assert projected["status"] == "unmet"
        assert projected["requirements"] == []


def test_rejected_report_has_only_renderable_fields():
    from openprogram.programs.tools.planning.goal.presentation import candidate_presentation
    value = candidate_presentation({"id": "c", "status": "unmet", "report": {
        "requirements": [{"id": [], "reason": {}}, {"id": "objective", "verdict": {}, "reason": {}, "evidence": [None, {}, "message:a"]}],
    }})
    assert value["requirements"] == [{"id": "objective", "text": "objective", "verdict": "unknown", "reason": "", "evidence": ["message:a"]}]
