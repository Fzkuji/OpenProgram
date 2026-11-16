"""Tests for cron entries and their non-interactive execution boundary.

Agent-prompt entries already have their create/list/delete semantics
exercised implicitly by other tests; this file pins the new shell path:

- creating with `command` persists the entry with the right shape
- creating with both prompt+command is rejected
- creating with neither is rejected
- list-mode preview renders shell entries with the ``$`` marker
- worker uses the frozen execution spec and explicit sandbox policy
"""
from __future__ import annotations

import os

import pytest

from openprogram.programs.tools.jobs.cron import cron as cron_tool
from openprogram.programs.tools.jobs.cron import worker


@pytest.fixture
def sched(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.permissions import authority

    path = tmp_path / "schedule.json"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setattr(
        cron_tool, "_caller_authority", authority.local_owner_authority,
    )
    monkeypatch.setenv(cron_tool.DEFAULT_CRON_ENV, str(path))
    yield path


def _create(**kw) -> str:
    return cron_tool.execute(action="create", **kw)


@pytest.mark.parametrize("argv", [
    ["scheduler-worker"],
    ["scheduler-worker", "--once"],
    ["scheduler-worker", "--list"],
    ["cron-worker"],
    ["cron-worker", "--once"],
    ["cron-worker", "--list"],
])
def test_scheduler_worker_bypasses_tui_stdio_redirect(argv):
    from openprogram.interfaces.cli.application import _looks_like_tui_invocation

    assert _looks_like_tui_invocation(argv) is False


def test_create_with_command_persists_command_field(sched):
    out = _create(cron="*/5 * * * *", command="echo hi", cwd=str(sched.parent))
    assert "Created scheduler task" in out
    entries = cron_tool._load(str(sched))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["command"] == "echo hi"
    assert "prompt" not in entry
    spec = entry["execution"]
    assert spec["kind"] == "command"
    assert spec["command"] == "echo hi"
    assert spec["cwd"] == str(sched.parent.resolve())
    assert "policy" not in spec and "policy_hash" not in spec
    assert len(spec["spec_hash"]) == 64
    assert len(spec["signature"]) == 64
    assert spec["job_authority"]["principal_id"].startswith("owner/install/")
    assert spec["job_authority"]["authority_tier"] == "owner"


def test_paired_channel_cannot_create_or_delete_jobs(sched, monkeypatch):
    from openprogram.runtime.permissions.authority import paired_channel_authority

    assert "Created scheduler task" in _create(cron="@daily", command="echo kept")
    entry_id = cron_tool._load(str(sched))[0]["id"]
    monkeypatch.setattr(
        cron_tool,
        "_caller_authority",
        lambda: paired_channel_authority("telegram", "main", "u456", "B"),
    )
    out = _create(cron="@daily", command="echo no")
    assert "authority" in out.lower()
    deleted = cron_tool.execute(action="delete", id=entry_id)
    assert "authority" in deleted.lower()
    entries = cron_tool._load(str(sched))
    assert len(entries) == 1 and entries[0]["id"] == entry_id


def test_create_rejects_both_prompt_and_command(sched):
    out = _create(cron="@daily", prompt="be productive", command="echo hi")
    assert "exactly one of `prompt`" in out.lower()
    assert not cron_tool._load(str(sched))


def test_create_rejects_neither(sched):
    out = _create(cron="@daily")
    assert "exactly one of `prompt`" in out.lower()
    assert not cron_tool._load(str(sched))


def test_list_shows_shell_marker(sched):
    _create(cron="@hourly", command="touch /tmp/heartbeat")
    _create(cron="@daily",  prompt="summarize today")
    out = cron_tool.execute(action="list")
    # command entry uses $, prompt entry uses >
    assert "$ touch /tmp/heartbeat" in out
    assert "> summarize today" in out


def test_worker_spawn_uses_frozen_command_and_the_configured_sandbox(
    sched, tmp_path, monkeypatch,
):
    marker = tmp_path / "ran.txt"
    tampered = tmp_path / "tampered.txt"
    _create(cron="@hourly", command=f"echo ok > {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    entry["command"] = f"echo bad > {tampered}"
    captured = {}

    def fake_invocation(command, cwd=None, *, policy=None):
        captured.update(command=command, cwd=cwd, policy=policy)
        return command, True, None, True

    monkeypatch.setattr(worker, "_invocation", fake_invocation)
    log_dir = tmp_path / "logs"
    proc = worker._spawn(entry, str(log_dir))
    assert proc is not None
    proc.wait(timeout=5)
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").strip() == "ok"
    assert not tampered.exists()
    assert captured["cwd"] == str(tmp_path.resolve())
    assert "policy" in captured


def test_worker_direct_command_really_runs(sched, tmp_path):
    marker = tmp_path / "real.txt"
    _create(cron="@hourly", command=f"printf real > {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    log_dir = tmp_path / "real-logs"
    proc = worker._spawn(entry, str(log_dir))
    assert proc is not None
    exit_code = proc.wait(timeout=10)
    assert exit_code == 0, next(log_dir.iterdir()).read_text(encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "real"


def test_worker_direct_needs_no_sandbox_when_none_is_configured(
    sched, tmp_path, monkeypatch,
):
    marker = tmp_path / "ran"
    _create(cron="@hourly", command=f"touch {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    monkeypatch.setattr(worker._sandbox, "unavailable_reason", lambda: "missing")
    proc = worker._spawn(entry, str(tmp_path / "run-logs"))
    assert proc is not None
    proc.wait(timeout=5)
    assert marker.exists()


def test_worker_refuses_a_tampered_execution_spec(sched, tmp_path):
    _create(cron="@hourly", command="echo ok", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    entry["execution"]["command"] = "echo changed"
    assert worker._spawn(entry, str(tmp_path / "logs")) is None
    log = next((tmp_path / "logs").iterdir()).read_text(encoding="utf-8")
    assert "refused" in log.lower() and "spec hash" in log.lower()


def _as_version_one(entry, speaker_id):
    """Rewrite a stored spec the way the previous format signed it."""
    spec = entry["execution"]
    spec["version"] = 1
    authority = spec["job_authority"]
    authority.pop("authority_hash")
    authority["creator"]["speaker_id"] = speaker_id
    authority["authority_hash"] = cron_tool._json_hash(authority)
    spec.pop("spec_hash")
    spec.pop("signature")
    spec["spec_hash"] = cron_tool._json_hash(spec)
    spec["signature"] = cron_tool._signature(spec, create_key=False)
    return spec


def test_a_task_set_from_a_chat_in_the_old_format_no_longer_runs(sched, tmp_path):
    _create(cron="@hourly", command="echo ok", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    verified, error = cron_tool._verify_execution_spec(_as_version_one(entry, "7000000"))
    assert verified is None and "set it again" in error
    assert worker._spawn(entry, str(tmp_path / "logs")) is None
    assert "set it again" in cron_tool.read_refusals()[entry["id"]]


def test_a_task_the_local_owner_set_in_the_old_format_still_runs(sched, tmp_path):
    _create(cron="@hourly", command="echo ok", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    verified, error = cron_tool._verify_execution_spec(_as_version_one(entry, "owner/local"))
    assert error is None and verified is not None


def test_a_run_refused_at_verification_says_why_on_the_task(sched, tmp_path):
    work = tmp_path / "gone"
    work.mkdir()
    _create(cron="@hourly", command="echo ok", cwd=str(work))
    entry = cron_tool._load(str(sched))[0]
    work.rmdir()
    assert worker._spawn(entry, str(tmp_path / "logs")) is None
    assert "cwd does not exist" in cron_tool.read_refusals()[entry["id"]]
    work.mkdir()
    worker._spawn(entry, str(tmp_path / "logs")).wait(timeout=10)
    assert entry["id"] not in cron_tool.read_refusals()


def test_worker_refuses_rehashed_tampering_without_owner_signature(sched, tmp_path):
    _create(cron="@hourly", command="echo ok", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    entry["execution"]["command"] = "echo changed"
    unsigned = {
        key: value for key, value in entry["execution"].items()
        if key not in {"spec_hash", "signature"}
    }
    entry["execution"]["spec_hash"] = cron_tool._json_hash(unsigned)
    assert worker._spawn(entry, str(tmp_path / "logs")) is None
    log = next((tmp_path / "logs").iterdir()).read_text(encoding="utf-8")
    assert "signature" in log.lower()


def test_prompt_job_rebuilds_a_noninteractive_cron_turn(sched, tmp_path,
                                                         monkeypatch):
    _create(cron="@daily", prompt="summarize", cwd=str(tmp_path))
    spec = cron_tool._load(str(sched))[0]["execution"]
    seen = {}

    class Result:
        failed = False
        final_text = "done"
        error = None

    def fake_turn(req, **_):
        seen["req"] = req
        return Result()

    monkeypatch.setattr("openprogram.runtime.conversation.turn_runtime.process_user_turn", fake_turn)
    log_path = tmp_path / "prompt.log"
    worker._run_prompt_job(spec, str(log_path))
    req = seen["req"]
    assert req.source == "scheduler"
    from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
    assert req.permission_mode == DEFAULT_PERMISSION
    assert req.advance_head is False
    assert req.user_text == "summarize"
    assert req.speaker_kind == "runtime"
    assert req.interaction == "non-interactive"
    assert req.principal_id == spec["job_authority"]["principal_id"]
    assert req.authority_tier == "owner"


@pytest.mark.parametrize(("rule_kind", "reason_code"), [
    ("deny", "RULE_DENY"),
    ("ask", "NO_ONE_TO_APPROVE"),
])
def test_prompt_job_applies_memory_permission_rules(
    sched, tmp_path, monkeypatch, rule_kind, reason_code,
):
    import asyncio

    from openprogram.runtime.permissions.approvals import approval as _approval
    from openprogram.runtime.permissions.modes import PermissionRules
    from openprogram.models.providers.types import AgentTool
    from openprogram.runtime.permissions import rule as permission_rule

    _create(cron="@daily", prompt="update memory", cwd=str(tmp_path))
    spec = cron_tool._load(str(sched))[0]["execution"]
    rules = PermissionRules(**{rule_kind: ["memory_update"]})
    monkeypatch.setattr(
        permission_rule, "load_merged_rules", lambda _session_id: rules,
    )
    seen = {}

    async def execute(_call_id, _args, _cancel, _on_update):
        raise AssertionError("blocked memory_update must not execute")

    def fake_turn(req, **_):
        tool = AgentTool(
            name="memory_update",
            description="",
            parameters={},
            label="memory_update",
            execute=execute,
        )
        result = asyncio.run(
            _approval.wrap_with_approval(
                tool, req, lambda _event: None,
            ).execute("call", {}, None, None)
        )
        seen.update(req=req, result=result)
        return type("Result", (), {
            "failed": False, "final_text": "done", "error": None,
        })()

    monkeypatch.setattr("openprogram.runtime.conversation.turn_runtime.process_user_turn", fake_turn)
    worker._run_prompt_job(spec, str(tmp_path / f"{rule_kind}.log"))

    assert seen["req"].permission_rules == rules
    assert seen["result"].details["reason_code"] == reason_code


def test_prompt_job_uses_the_sandbox_configured_when_it_runs(
    sched, tmp_path, monkeypatch,
):
    """The worker runs what the owner has set now, not a copy from creation."""
    from openprogram.host import sandbox

    _create(cron="@daily", prompt="summarize", cwd=str(tmp_path))
    spec = cron_tool._load(str(sched))[0]["execution"]
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"sandbox": {"mode": "danger-full-access"}},
    )
    seen = {}

    class Result:
        failed = False
        final_text = "done"
        error = None

    def fake_turn(_req, **_kw):
        seen["policy"] = sandbox.resolve_policy()
        return Result()

    monkeypatch.setattr("openprogram.runtime.conversation.turn_runtime.process_user_turn", fake_turn)
    worker._run_prompt_job(spec, str(tmp_path / "policy.log"))
    assert "policy" in seen and seen["policy"] is None


def test_worker_spawn_prompt_uses_managed_prompt_entry(
    sched, tmp_path, monkeypatch,
):
    _create(cron="@daily", prompt="summarize", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    seen = {}

    class Result:
        failed = False
        final_text = "done"
        error = None

    def fake_turn(req, **_):
        seen["req"] = req
        return Result()

    monkeypatch.setattr("openprogram.runtime.conversation.turn_runtime.process_user_turn", fake_turn)

    class InlineProcess:
        pid = 123

        def __init__(self, *, target, args, name):
            self.target, self.args, self.name = target, args, name

        def start(self):
            self.target(*self.args)

    import queue

    monkeypatch.setattr(worker.multiprocessing, "Process", InlineProcess)
    monkeypatch.setattr(worker.multiprocessing, "Queue", queue.Queue)
    proc = worker._spawn(entry, str(tmp_path / "prompt-logs"))
    assert proc is not None
    # The relay that serves the job ends on its own once the job is gone;
    # wait for it so its clean-up never outlives this test.
    import threading

    for thread in threading.enumerate():
        if thread.name == "cron-job-relay":
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert seen["req"].source == "scheduler"
    assert "done" in next((tmp_path / "prompt-logs").iterdir()).read_text(
        encoding="utf-8"
    )


def test_worker_spawn_returns_none_for_empty_entry(tmp_path):
    assert worker._spawn({"id": "x", "cron": "@daily"}, str(tmp_path / "logs")) is None


@pytest.fixture
def chat_turn(tmp_path, monkeypatch):
    """A real chat: the owner's message and the reply its tools run under."""
    import openprogram.state.paths as paths
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.execution.run_control import binding
    from openprogram.runtime.context import session_db
    from openprogram.state import store as chat_store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    db = session_db.SessionDB(tmp_path / "sessions")
    db.create_session("chat", "main")
    owner = authority.local_owner_authority()
    db.append_message("chat", {
        "id": "ask", "role": "user", "content": "check hourly", "timestamp": 0,
        "predecessor": "ROOT", **owner,
    })
    db.append_message("chat", {
        "id": "ask_reply", "role": "assistant", "content": "", "timestamp": 1,
        "predecessor": "ask", **authority.runtime_authority(owner, "agent/main"),
    })
    db.append_message("chat", {
        "id": "delegated", "role": "user", "content": "sub-agent prompt",
        "timestamp": 2, "predecessor": "ask_reply",
        **authority.runtime_authority(owner, "agent/main"),
    })
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    session_token = binding._current_session_id.set("chat")
    try:
        yield chat_store
    finally:
        binding._current_session_id.reset(session_token)
        db.close()
        authority._reset_owner_cache_for_tests()


def test_a_schedule_created_from_the_owners_chat_turn_is_the_owners(chat_turn):
    token = chat_turn._current_turn_id.set("ask_reply")
    try:
        authority, error = cron_tool._authorize("create")
    finally:
        chat_turn._current_turn_id.reset(token)
    assert error is None
    assert authority["speaker_kind"] == "owner"
    assert authority["interaction"] == "interactive"


def test_a_sub_agent_or_always_on_turn_of_the_owner_can_schedule(chat_turn):
    """A runtime speaker holding the owner's principal is still the owner."""
    token = chat_turn._current_turn_id.set("delegated")
    try:
        authority, error = cron_tool._authorize("create")
    finally:
        chat_turn._current_turn_id.reset(token)
    assert error is None
    assert authority["speaker_kind"] == "runtime"
    assert authority["interaction"] == "non-interactive"
    assert authority["authority_tier"] == "owner"
