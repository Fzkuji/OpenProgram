"""A worktree created mid-turn must not break resuming after an approval."""
from openprogram.runtime.conversation.turn_runtime import loop_runner


def test_safe_point_records_the_location_a_resume_resolves(monkeypatch):
    location = {"workdir": "/repo", "project": None, "worktree": None}
    monkeypatch.setattr("openprogram.runtime.conversation.workdir.runtime_location_for",
                        lambda session_id, use_context=True: dict(location) if not use_context else None)
    started = {"model": {"id": "m"}, "request_semantics": {"_execution_revision_id": "r",
                                                          "execution_location": dict(location)}}
    # worktree_create ran between the turn start and the approval pause.
    location.update(workdir="/wt", worktree={"id": "wt_1", "worktree_path": "/wt"})
    snapshot = loop_runner.with_current_location(started, "s")
    assert snapshot["request_semantics"]["execution_location"] == location
    assert snapshot["request_semantics"]["_execution_revision_id"] == "r"
    assert started["request_semantics"]["execution_location"]["worktree"] is None


def test_snapshot_without_request_semantics_is_unchanged():
    snapshot = {"model": {"id": "m"}}
    assert loop_runner.with_current_location(snapshot, "s") is snapshot
