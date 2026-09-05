"""Public Scheduler contracts, including stable Memory references."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.agent import authority
    from openprogram.programs.tools.jobs.cron import cron as cron_tool

    schedule = tmp_path / "scheduler" / "tasks.json"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setenv("OPENPROGRAM_SCHEDULER_PATH", str(schedule))
    monkeypatch.delenv(cron_tool.DEFAULT_CRON_ENV, raising=False)
    return tmp_path, schedule


def test_scheduler_creates_once_recurring_and_monitor_tasks(scheduler_env):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    once = service.create_task(
        title="Submit rebuttal",
        task_type="once",
        prompt="Remind me to submit the rebuttal.",
        run_at="2026-08-15T09:00:00+08:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    recurring = service.create_task(
        title="Weekly review",
        task_type="recurring",
        prompt="Review this week's work.",
        cron="0 16 * * 5",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    monitor = service.create_task(
        title="Follow-up monitor",
        task_type="monitor",
        prompt="Check whether the follow-up changed.",
        cron="0 9 * * 1-5",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )

    assert once["type"] == "once" and "run_at" in once and "cron" not in once
    assert recurring["type"] == "recurring" and recurring["cron"] == "0 16 * * 5"
    assert monitor["type"] == "monitor" and monitor["cron"] == "0 9 * * 1-5"
    assert [row["title"] for row in service.list_tasks()] == [
        "Submit rebuttal",
        "Weekly review",
        "Follow-up monitor",
    ]


@pytest.mark.parametrize("cron", ["x x x x x", "60 24 32 13 8", "*/0 * * * *"])
def test_scheduler_rejects_cron_that_the_worker_cannot_execute(
    scheduler_env, cron,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    with pytest.raises(ValueError, match="cron"):
        service.create_task(
            title="Invalid schedule",
            task_type="recurring",
            prompt="This must not be persisted.",
            cron=cron,
            cwd=str(tmp_path),
            authority=local_owner_authority(),
        )


def test_scheduler_default_path_is_profile_scoped(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.programs.tools.jobs.cron import cron as cron_tool

    monkeypatch.delenv(cron_tool.DEFAULT_SCHEDULER_ENV, raising=False)
    monkeypatch.delenv(cron_tool.DEFAULT_CRON_ENV, raising=False)
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    assert cron_tool._resolve_path() == str(
        tmp_path / "profile" / "scheduler" / "tasks.json"
    )


def test_scheduler_moves_legacy_cron_store_with_signing_state(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.programs.tools.jobs.cron import cron as cron_tool

    monkeypatch.delenv(cron_tool.DEFAULT_SCHEDULER_ENV, raising=False)
    monkeypatch.delenv(cron_tool.DEFAULT_CRON_ENV, raising=False)
    state = tmp_path / "profile"
    legacy = state / "cron"
    legacy.mkdir(parents=True)
    (legacy / "schedule.json").write_text('{"entries": []}\n')
    (legacy / ".signing-key").write_bytes(b"k" * 32)
    (legacy / "worker-state.json").write_text("{}\n")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    resolved = cron_tool._resolve_path()
    target = state / "scheduler"
    assert resolved == str(target / "tasks.json")
    assert (target / ".signing-key").read_bytes() == b"k" * 32
    assert (target / "worker-state.json").is_file()
    assert not (legacy / "schedule.json").exists()


def test_legacy_cron_store_migration_is_concurrency_safe(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.programs.tools.jobs.cron import cron as cron_tool

    monkeypatch.delenv(cron_tool.DEFAULT_SCHEDULER_ENV, raising=False)
    monkeypatch.delenv(cron_tool.DEFAULT_CRON_ENV, raising=False)
    state = tmp_path / "profile"
    legacy = state / "cron"
    (legacy / "logs").mkdir(parents=True)
    (legacy / "schedule.json").write_text('{"entries": []}\n')
    (legacy / ".signing-key").write_bytes(b"k" * 32)
    (legacy / "worker-state.json").write_text("{}\n")
    (legacy / "logs" / "old.log").write_text("old\n")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    original_move = cron_tool.shutil.move

    def slow_move(source, target):
        time.sleep(0.02)
        return original_move(source, target)

    monkeypatch.setattr(cron_tool.shutil, "move", slow_move)
    with ThreadPoolExecutor(max_workers=2) as pool:
        resolved = list(pool.map(lambda _index: cron_tool._resolve_path(), range(2)))
    target = state / "scheduler"
    assert resolved == [str(target / "tasks.json")] * 2
    assert (target / ".signing-key").read_bytes() == b"k" * 32
    assert (target / "worker-state.json").is_file()
    assert (target / "logs" / "old.log").read_text() == "old\n"


def test_scheduler_resolves_memory_refs_at_execution_time(scheduler_env):
    from openprogram.memory import references, store

    _tmp_path, _schedule = scheduler_env
    topics = store.ensure() / "topics"
    (topics / "projects").mkdir(parents=True)
    (topics / "projects" / "memory.md").write_text(
        "# Memory\n\nUse the unified API.[^src-a] ^mem-project\n\n"
        "[^src-a]: Time: `2026-08-14`; Sources: [D1:1](D1:1)\n",
        encoding="utf-8",
    )
    ref = {"workspace_id": store.workspace_id(), "memory_id": "mem-project"}

    first = references.resolve([ref])
    assert first[0]["content"] == "Use the unified API."
    (topics / "projects" / "memory.md").write_text(
        "# Memory\n\nUse the Scheduler API.[^src-a] ^mem-project\n\n"
        "[^src-a]: Time: `2026-08-14`; Sources: [D1:1](D1:1)\n",
        encoding="utf-8",
    )
    second = references.resolve([ref])
    assert second[0]["content"] == "Use the Scheduler API."
    context = references.render_context([ref])
    assert "topics/projects/memory.md" in context
    assert "memory_status" in context
    assert "memory_update" in context
    assert "delete it only when no durable value remains" in context
    with pytest.raises(ValueError, match="workspace"):
        references.resolve([
            {"workspace_id": "w-deadbeef", "memory_id": "mem-project"}
        ])


def test_scheduler_context_keeps_every_ref_when_content_is_truncated(
    scheduler_env,
):
    from openprogram.memory import references, store

    _tmp_path, _schedule = scheduler_env
    topics = store.ensure() / "topics"
    long_content = "A" * 11_800
    (topics / "first.md").write_text(
        "# First\n\n"
        f"{long_content}[^e-first] ^mem-first\n\n"
        "[^e-first]: Time: `2026-08-14`; Sources: [D1:1](D1:1)\n",
        encoding="utf-8",
    )
    (topics / "second.md").write_text(
        "# Second\n\nSECOND RECORD[^e-second] ^mem-second\n\n"
        "[^e-second]: Time: `2026-08-14`; Sources: [D1:1](D1:1)\n",
        encoding="utf-8",
    )
    workspace_id = store.workspace_id()
    context = references.render_context([
        {"workspace_id": workspace_id, "memory_id": "mem-first"},
        {"workspace_id": workspace_id, "memory_id": "mem-second"},
    ])

    assert len(context) <= 12_000
    assert "[mem-first] topics/first.md" in context
    assert "[mem-second] topics/second.md" in context
    assert "SECOND RECORD" in context
    assert "[content truncated]" in context


def test_once_task_fires_only_once_and_recurring_task_keeps_running(
    scheduler_env, monkeypatch,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    once = service.create_task(
        title="One time",
        task_type="once",
        command="echo once",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    recurring = service.create_task(
        title="Hourly",
        task_type="recurring",
        command="echo hourly",
        cron="0 * * * *",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    fired = []

    class Proc:
        pid = 7

    monkeypatch.setattr(
        worker,
        "_spawn",
        lambda entry, _log_dir: fired.append(entry["id"]) or Proc(),
    )
    state = {}
    now = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    assert worker._tick(state, now=now) == 2
    assert worker._tick(state, now=now) == 0
    assert worker._tick(state, now=datetime(2026, 8, 14, 11, 0, tzinfo=timezone.utc)) == 1
    assert fired == [once["id"], recurring["id"], recurring["id"]]


def test_worker_persists_each_claim_and_continues_after_spawn_failure(
    scheduler_env, monkeypatch,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    first = service.create_task(
        title="First",
        task_type="once",
        command="echo first",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    second = service.create_task(
        title="Second",
        task_type="once",
        command="echo second",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )

    class Proc:
        pid = 9

    def spawn(entry, _log_dir):
        if entry["id"] == first["id"]:
            raise RuntimeError("spawn failed")
        return Proc()

    monkeypatch.setattr(worker, "_spawn", spawn)
    state = {}
    now = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    assert worker._tick(state, now=now) == 1
    persisted = worker._load_state()
    assert first["id"] not in persisted
    assert persisted[second["id"]]["last_fired_minute"] == "2026-08-14T10:00"


@pytest.mark.parametrize(
    ("failure_call", "expected_calls", "expected_sleeps"),
    [
        (1, [True, False], [1.0, 1.0]),
        (2, [True, False, False], [1.0, 1.0, 1.0, 1.0]),
    ],
)
def test_worker_loop_recovers_from_one_transient_tick_failure(
    scheduler_env,
    monkeypatch,
    caplog,
    failure_call,
    expected_calls,
    expected_sleeps,
):
    from openprogram.programs.tools.jobs.cron import worker

    stop = threading.Event()
    tick_calls = []
    sleeps = []
    elapsed = 0.0

    class NearMinute(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 14, 10, 0, 58)
            return value if tz is None else value.replace(tzinfo=tz)

    def tick(_state, *, reboot=False):
        tick_calls.append(reboot)
        if len(tick_calls) == failure_call:
            raise RuntimeError("transient tick")
        if len(tick_calls) == failure_call + 1:
            stop.set()
        return 0

    monkeypatch.setattr(worker, "_load_state", lambda: {})
    monkeypatch.setattr(worker, "_tick", tick)
    def sleep(seconds):
        nonlocal elapsed
        sleeps.append(seconds)
        elapsed += seconds

    monkeypatch.setattr(worker, "dt", SimpleNamespace(datetime=NearMinute))
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep),
    )

    with caplog.at_level("WARNING", logger=worker.__name__):
        worker.run_forever(stop)

    assert tick_calls == expected_calls
    assert sleeps == expected_sleeps
    records = [record for record in caplog.records if record.name == worker.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == (
        "scheduler tick failed: RuntimeError: transient tick"
    )
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


def test_worker_loop_rechecks_elapsed_time_after_oversleep(
    scheduler_env, monkeypatch,
):
    from openprogram.programs.tools.jobs.cron import worker

    stop = threading.Event()
    tick_calls = []
    sleeps = []
    elapsed = 0.0

    class StartOfMinute(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 14, 10, 0, 0)
            return value if tz is None else value.replace(tzinfo=tz)

    def tick(_state, *, reboot=False):
        tick_calls.append(reboot)
        if len(tick_calls) == 1:
            raise RuntimeError("transient tick")
        stop.set()
        return 0

    def sleep(seconds):
        nonlocal elapsed
        sleeps.append(seconds)
        elapsed += 70.0

    monkeypatch.setattr(worker, "_load_state", lambda: {})
    monkeypatch.setattr(worker, "_tick", tick)
    monkeypatch.setattr(worker, "dt", SimpleNamespace(datetime=StartOfMinute))
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep),
    )

    worker.run_forever(stop)

    assert tick_calls == [True, False]
    assert sleeps == [1.0]


def test_worker_loop_does_not_swallow_base_exception(
    scheduler_env, monkeypatch,
):
    from openprogram.programs.tools.jobs.cron import worker

    monkeypatch.setattr(worker, "_load_state", lambda: {})

    def interrupt(_state, *, reboot=False):
        raise KeyboardInterrupt(f"interrupt reboot={reboot}")

    monkeypatch.setattr(worker, "_tick", interrupt)

    with pytest.raises(KeyboardInterrupt, match="interrupt reboot=True"):
        worker.run_forever(threading.Event())


def test_run_once_keeps_tick_fail_fast(scheduler_env, monkeypatch):
    from openprogram.programs.tools.jobs.cron import worker

    monkeypatch.setattr(worker, "_load_state", lambda: {})

    def fail(_state):
        raise RuntimeError("once failed")

    monkeypatch.setattr(worker, "_tick", fail)

    with pytest.raises(RuntimeError, match="once failed"):
        worker.run_once()


def test_two_workers_claim_a_once_task_only_once(scheduler_env, monkeypatch):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    service.create_task(
        title="Claim once",
        task_type="once",
        command="echo once",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )

    class Proc:
        pid = 10

    spawned = []
    monkeypatch.setattr(
        worker,
        "_spawn",
        lambda entry, _log_dir: spawned.append(entry["id"]) or Proc(),
    )
    now = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: worker._tick({}, now=now), range(2)))
    assert sum(results) == 1
    assert len(spawned) == 1


def test_claim_write_failure_does_not_spawn_or_block_later_task(
    scheduler_env, monkeypatch,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    first = service.create_task(
        title="State failure",
        task_type="once",
        command="echo no",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    second = service.create_task(
        title="Still runs",
        task_type="once",
        command="echo yes",
        run_at="2026-08-14T10:00:00+00:00",
        cwd=str(tmp_path),
        authority=local_owner_authority(),
    )
    original_save = worker._save_state
    save_calls = 0

    def save(state):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("disk unavailable")
        original_save(state)

    class Proc:
        pid = 11

    spawned = []
    monkeypatch.setattr(worker, "_save_state", save)
    monkeypatch.setattr(
        worker,
        "_spawn",
        lambda entry, _log_dir: spawned.append(entry["id"]) or Proc(),
    )
    now = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    assert worker._tick({}, now=now) == 1
    assert spawned == [second["id"]]
    assert first["id"] not in worker._load_state()


def test_scheduler_concurrent_creates_do_not_lose_tasks(scheduler_env, monkeypatch):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.programs.tools.jobs.cron import cron as cron_tool
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    original_load = cron_tool._load

    def slow_load(path):
        rows = original_load(path)
        time.sleep(0.01)
        return rows

    monkeypatch.setattr(cron_tool, "_load", slow_load)

    def create(index):
        return service.create_task(
            title=f"Task {index}",
            task_type="recurring",
            prompt=f"Run task {index}",
            cron="0 * * * *",
            cwd=str(tmp_path),
            authority=local_owner_authority(),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(create, range(8)))
    assert len(service.list_tasks()) == 8


def test_scheduler_rejects_unresolved_memory_refs_before_persisting(scheduler_env):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.memory import store
    from openprogram.scheduler import service

    tmp_path, _schedule = scheduler_env
    missing = {
        "workspace_id": store.workspace_id(),
        "memory_id": "mem-does-not-exist",
    }
    with pytest.raises(ValueError, match="not found"):
        service.create_task(
            title="Invalid MemoryRef",
            task_type="recurring",
            prompt="Use missing memory.",
            cron="0 * * * *",
            memory_refs=[missing],
            cwd=str(tmp_path),
            authority=local_owner_authority(),
        )
    assert service.list_tasks() == []


def test_legacy_open_commitments_migrate_once_and_archive_source(
    scheduler_env,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.memory import store
    from openprogram.scheduler import migration, service

    _tmp_path, _schedule = scheduler_env
    memory_root = store.ensure()
    legacy = memory_root / "commitments.jsonl"
    legacy.write_text(
        json.dumps({
            "id": "com_0123456789abcdef",
            "text": "Submit the rebuttal.",
            "due": "2026-08-15",
            "speaker_id": "owner",
            "source": "src_1",
            "source_quote": "I will submit the rebuttal.",
            "status": "open",
            "status_source": None,
            "status_quote": None,
            "status_changed_at": None,
            "notification_steps": [],
        }) + "\n",
        encoding="utf-8",
    )

    assert migration.migrate_legacy_commitments(
        memory_root=memory_root,
        cwd=str(memory_root),
        authority=local_owner_authority(),
    ) == 1
    assert migration.migrate_legacy_commitments(
        memory_root=memory_root,
        cwd=str(memory_root),
        authority=local_owner_authority(),
    ) == 0
    tasks = service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["type"] == "once"
    assert tasks[0]["legacy_commitment_id"] == "com_0123456789abcdef"
    assert tasks[0]["run_at"].startswith("2026-08-15T09:00:00")
    assert not legacy.exists()
    assert migration.legacy_archive_path().is_file()


def test_legacy_commitment_migration_is_serialized(scheduler_env):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.memory import store
    from openprogram.scheduler import migration, service

    _tmp_path, _schedule = scheduler_env
    memory_root = store.ensure()
    (memory_root / "commitments.jsonl").write_text(
        json.dumps({
            "id": "com_1122334455667788",
            "text": "Migrate once.",
            "due": "2026-08-15",
            "status": "open",
        }) + "\n",
        encoding="utf-8",
    )

    def migrate(_index):
        return migration.migrate_legacy_commitments(
            memory_root=memory_root,
            cwd=str(memory_root),
            authority=local_owner_authority(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(migrate, range(2)))
    assert results == [0, 1]
    assert len(service.list_tasks()) == 1


def test_scheduler_tool_names_and_authority_compatibility():
    from openprogram.agent.authority import capability_for_tool
    from openprogram.programs import agent_tools

    default_names = {tool.name for tool in agent_tools()}
    explicit_names = {tool.name for tool in agent_tools(names=["scheduler", "cron"])}
    assert "scheduler" in default_names
    assert explicit_names == {"scheduler", "cron"}
    for name in ("scheduler", "cron"):
        assert capability_for_tool(name, {"action": "create"}) == "schedule.create"
        assert capability_for_tool(name, {"action": "update"}) == "schedule.manage"
        assert capability_for_tool(name, {"action": "delete"}) == "schedule.manage"
        assert capability_for_tool(name, {"action": "list"}) == "fs.read"


def test_legacy_migration_preserves_a_new_source_when_archive_exists(
    scheduler_env,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.memory import store
    from openprogram.scheduler import migration

    _tmp_path, _schedule = scheduler_env
    memory_root = store.ensure()
    source = memory_root / "commitments.jsonl"
    source.write_text(json.dumps({
        "id": "com_fedcba9876543210",
        "text": "Review the new source.",
        "due": None,
        "status": "open",
    }) + "\n", encoding="utf-8")
    archive = migration.legacy_archive_path()
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("previous archive\n", encoding="utf-8")

    assert migration.migrate_legacy_commitments(
        memory_root=memory_root,
        cwd=str(memory_root),
        authority=local_owner_authority(),
    ) == 1
    assert archive.read_text(encoding="utf-8") == "previous archive\n"
    preserved = list(archive.parent.glob("commitments.*.jsonl"))
    assert len(preserved) == 1
    assert "com_fedcba9876543210" in preserved[0].read_text(encoding="utf-8")
    assert not source.exists()


def test_legacy_migration_refuses_to_overwrite_a_conflicting_hash_archive(
    scheduler_env,
):
    from openprogram.agent.authority import local_owner_authority
    from openprogram.memory import store
    from openprogram.scheduler import migration

    _tmp_path, _schedule = scheduler_env
    memory_root = store.ensure()
    source = memory_root / "commitments.jsonl"
    source_bytes = (json.dumps({
        "id": "com_0011223344556677",
        "text": "Keep both archives.",
        "due": None,
        "status": "open",
    }) + "\n").encode()
    source.write_bytes(source_bytes)
    archive = migration.legacy_archive_path()
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("previous archive\n", encoding="utf-8")
    digest = hashlib.sha256(source_bytes).hexdigest()
    collision = archive.with_name(f"commitments.{digest}.jsonl")
    collision.write_text("different content\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="archive collision"):
        migration.migrate_legacy_commitments(
            memory_root=memory_root,
            cwd=str(memory_root),
            authority=local_owner_authority(),
        )
    assert source.read_bytes() == source_bytes
    assert collision.read_text(encoding="utf-8") == "different content\n"


def test_memory_status_contract_has_no_commitments(scheduler_env):
    from openprogram.memory import store
    from openprogram.memory.retrieval import inspect

    status = inspect.status(store.ensure())
    assert "commitments" not in status


def test_scheduler_rest_api_crud_omits_frozen_execution_spec(scheduler_env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.scheduler import service
    from openprogram.webui.routes import scheduler as routes

    tmp_path, _schedule = scheduler_env
    app = FastAPI()
    routes.register(app)
    client = TestClient(app)
    created = client.post("/api/scheduler/tasks", json={
        "title": "Daily brief",
        "type": "recurring",
        "prompt": "Summarize priorities.",
        "cron": "0 8 * * 1-5",
        "cwd": str(tmp_path),
    })
    assert created.status_code == 201, created.text
    task = created.json()
    assert "execution" not in task
    assert client.get("/api/scheduler/tasks").json()[0]["id"] == task["id"]
    detail = client.get(f"/api/scheduler/tasks/{task['id']}")
    assert detail.status_code == 200 and detail.json()["id"] == task["id"]
    assert "execution" not in detail.json()
    assert client.get("/api/scheduler/tasks/missing").status_code == 404
    paused = client.patch(
        f"/api/scheduler/tasks/{task['id']}", json={"enabled": False}
    )
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    updated = client.patch(
        f"/api/scheduler/tasks/{task['id']}",
        json={"prompt": "Summarize current priorities.", "memory_refs": []},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["prompt"] == "Summarize current priorities."
    assert "execution" not in updated.json()
    stored = service.get_task(task["id"])
    assert stored is not None
    assert stored["execution"]["prompt"] == "Summarize current priorities."
    assert stored["execution"]["signature"]
    assert client.delete(f"/api/scheduler/tasks/{task['id']}").status_code == 200
    assert client.get("/api/scheduler/tasks").json() == []
