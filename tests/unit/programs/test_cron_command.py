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
from pathlib import Path

import pytest

from openprogram.programs.tools.jobs.cron import cron as cron_tool
from openprogram.programs.tools.jobs.cron import worker


@pytest.fixture
def sched(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.agent import authority

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
    from openprogram.cli import _looks_like_tui_invocation

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
    assert len(spec["policy_hash"]) == 64
    assert len(spec["spec_hash"]) == 64
    assert len(spec["signature"]) == 64
    assert spec["job_authority"]["principal_id"].startswith("owner/install/")
    assert spec["job_authority"]["authority_tier"] == "owner"


def test_paired_channel_cannot_create_or_delete_jobs(sched, monkeypatch):
    from openprogram.agent.authority import paired_channel_authority

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
    assert "either `prompt`" in out.lower() or "not both" in out.lower()
    assert not cron_tool._load(str(sched))


def test_create_rejects_neither(sched):
    out = _create(cron="@daily")
    assert "required" in out.lower()
    assert not cron_tool._load(str(sched))


def test_list_shows_shell_marker(sched):
    _create(cron="@hourly", command="touch /tmp/heartbeat")
    _create(cron="@daily",  prompt="summarize today")
    out = cron_tool.execute(action="list")
    # command entry uses $, prompt entry uses >
    assert "$ touch /tmp/heartbeat" in out
    assert "> summarize today" in out


def test_worker_spawn_uses_frozen_command_and_explicit_policy(
    sched, tmp_path, monkeypatch,
):
    marker = tmp_path / "ran.txt"
    tampered = tmp_path / "tampered.txt"
    _create(cron="@hourly", command=f"echo ok > {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    entry["command"] = f"echo bad > {tampered}"
    captured = {}

    def fake_invocation(command, cwd=None, *, policy=None, force_sandbox=False):
        captured.update(command=command, cwd=cwd, policy=policy,
                        force_sandbox=force_sandbox)
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
    assert captured["force_sandbox"] is True
    assert captured["policy"] is not None
    assert entry["execution"]["policy_hash"] in next(log_dir.iterdir()).read_text(
        encoding="utf-8"
    )


def test_worker_direct_command_uses_real_forced_sandbox(sched, tmp_path):
    from openprogram import sandbox

    if not sandbox.is_available():
        pytest.skip("sandbox backend unavailable")
    marker = tmp_path / "real.txt"
    _create(cron="@hourly", command=f"printf real > {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    log_dir = tmp_path / "real-logs"
    proc = worker._spawn(entry, str(log_dir))
    assert proc is not None
    exit_code = proc.wait(timeout=10)
    assert exit_code == 0, next(log_dir.iterdir()).read_text(encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "real"


def test_worker_direct_refuses_when_sandbox_is_unavailable(
    sched, tmp_path, monkeypatch,
):
    marker = tmp_path / "must-not-exist"
    _create(cron="@hourly", command=f"touch {marker}", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    monkeypatch.setattr(worker._sandbox, "unavailable_reason", lambda: "missing")
    assert worker._spawn(entry, str(tmp_path / "refusal-logs")) is None
    assert not marker.exists()
    log = next((tmp_path / "refusal-logs").iterdir()).read_text(encoding="utf-8")
    assert "refused" in log.lower() and "missing" in log


def test_worker_refuses_a_tampered_execution_spec(sched, tmp_path):
    _create(cron="@hourly", command="echo ok", cwd=str(tmp_path))
    entry = cron_tool._load(str(sched))[0]
    entry["execution"]["command"] = "echo changed"
    assert worker._spawn(entry, str(tmp_path / "logs")) is None
    log = next((tmp_path / "logs").iterdir()).read_text(encoding="utf-8")
    assert "refused" in log.lower() and "spec hash" in log.lower()


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

    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", fake_turn)
    log_path = tmp_path / "prompt.log"
    worker._run_prompt_job(spec, str(log_path))
    req = seen["req"]
    assert req.source == "scheduler"
    assert req.permission_mode == "ask"
    assert req.advance_head is False
    assert req.user_text == "summarize"
    assert req.speaker_kind == "runtime"
    assert req.interaction == "non-interactive"
    assert req.principal_id == spec["job_authority"]["principal_id"]
    assert req.authority_tier == "owner"
    assert "policy_hash=" + spec["policy_hash"] in log_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(("rule_kind", "reason_code"), [
    ("deny", "PERMISSION_RULE_DENY"),
    ("ask", "APPROVAL_UNAVAILABLE_NON_INTERACTIVE"),
])
def test_prompt_job_applies_memory_permission_rules(
    sched, tmp_path, monkeypatch, rule_kind, reason_code,
):
    import asyncio

    from openprogram.agent.permissions import approval as _approval
    from openprogram.agent.session_config import PermissionRules
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.programs import permission_rule

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

    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", fake_turn)
    worker._run_prompt_job(spec, str(tmp_path / f"{rule_kind}.log"))

    assert seen["req"].permission_rules == rules
    assert seen["result"].details["reason_code"] == reason_code


def test_prompt_job_installs_the_frozen_policy_before_the_turn(
    sched, tmp_path, monkeypatch,
):
    """The verified policy must be applied, not merely checked.

    Until it is installed, in-process read tools inside the prompt turn
    see the host — the deny-read list is never consulted because no
    policy is active in this process.
    """
    from openprogram import sandbox

    _create(cron="@daily", prompt="summarize", cwd=str(tmp_path))
    spec = cron_tool._load(str(sched))[0]["execution"]
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    # Config says the sandbox is off; the frozen job policy must win.
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"sandbox": {"mode": "danger-full-access"}},
    )
    assert sandbox.resolve_policy() is None

    seen = {}

    class Result:
        failed = False
        final_text = "done"
        error = None

    def fake_turn(_req, **_kw):
        seen["policy"] = sandbox.resolve_policy()
        return Result()

    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", fake_turn)
    worker._run_prompt_job(spec, str(tmp_path / "policy.log"))

    active = seen["policy"]
    assert active is not None
    assert sandbox.policy_hash(active) == spec["policy_hash"]
    # And the read tools now actually enforce it.
    assert active.deny_read
    denied = os.path.expanduser(active.deny_read[0].replace("/**", "/probe"))
    assert sandbox.validate_read_path(denied)


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

    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", fake_turn)

    class InlineProcess:
        pid = 123

        def __init__(self, *, target, args, name):
            self.target, self.args, self.name = target, args, name

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(worker.multiprocessing, "Process", InlineProcess)
    proc = worker._spawn(entry, str(tmp_path / "prompt-logs"))
    assert proc is not None
    assert seen["req"].source == "scheduler"
    assert "done" in next((tmp_path / "prompt-logs").iterdir()).read_text(
        encoding="utf-8"
    )


def test_worker_spawn_returns_none_for_empty_entry(tmp_path):
    assert worker._spawn({"id": "x", "cron": "@daily"}, str(tmp_path / "logs")) is None
