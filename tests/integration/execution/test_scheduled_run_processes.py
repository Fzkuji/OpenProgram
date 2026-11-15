"""A scheduled task's own process and the Worker, as two real processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

_FAKE_MODEL = r'''
from openprogram.runtime.conversation import turn_runtime as loop
from tests.component.channels.test_channels_dispatch_inbound import _stub_model, make_delta_stream

_run = loop._run_loop_blocking


def _turn(*, req, history, on_event, cancel_event, **_):
    if req.source == "scheduler" and req.user_text == "check":
        from openprogram.runtime.conversation.sub_agent_run import run_agent_turn
        from openprogram.runtime.permissions.authority import local_owner_authority
        result = run_agent_turn(req.session_id, "look around", req.agent_id, authority=local_owner_authority())
        reply = f"sub-agent said: {result.final_text or result.error}"
    else:
        reply = "the screen is clear"
    return _run(req=req, history=history, on_event=on_event, cancel_event=cancel_event,
                stream_fn=make_delta_stream([reply]))


loop._run_loop_blocking = _turn
loop._resolve_model = lambda profile, override=None: _stub_model()
loop._load_agent_profile = lambda agent_id: {"id": agent_id, "system_prompt": "", "tools": []}
'''

_RUN_MODEL = r"""
import asyncio, os, threading, time
from pathlib import Path
from openprogram.programs.applications.browser import tabs
from openprogram.runtime.conversation import turn_runtime as loop
from tests.component.channels.test_channels_dispatch_inbound import _stub_model, make_delta_stream
from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText, ScriptedToolCall

FOLDER = Path(os.environ["RUN_FOLDER"])
_run = loop._run_loop_blocking
_scripted = ScriptedProvider()
_scripted.add_response(ScriptedToolCall(name="bash", id="call_1", arguments={
    "command": f"echo > '{FOLDER}/started'; sleep 60; echo > '{FOLDER}/slept'", "description": "wait"}))
_scripted.add_response(ScriptedText("done"))


def _waiting_model():
    async def stream(model, context, options):
        (FOLDER / "started").write_text("")
        await asyncio.sleep(120)
        yield None
    return stream


def _turn(*, req, history, on_event, cancel_event, **_):
    from openprogram.runtime.execution.run_control import get_current_execution_id
    (FOLDER / "execution").write_text(get_current_execution_id() or "")
    stream = {"wait in a tool": _scripted.stream_simple, "wait for the model": _waiting_model()}.get(req.user_text)
    return _run(req=req, history=history, on_event=on_event, cancel_event=cancel_event,
                stream_fn=stream or make_delta_stream(["the screen is clear"]))


def _close_now(execution_id):
    with (FOLDER / "closed").open("a") as handle:
        handle.write(execution_id + "\n")


def _close_later(execution_id):
    def close():
        time.sleep(2)
        _close_now("later:" + execution_id)
    threading.Thread(target=close, daemon=True).start()


loop._run_loop_blocking = _turn
loop._resolve_model = lambda profile, override=None: _stub_model()
loop._load_agent_profile = lambda agent_id: {"id": agent_id, "system_prompt": "", "tools": ["bash"]}
# The browser application's tab closing, observed: the App is not part of these processes.
tabs.close_run_tabs, tabs.close_run_tabs_soon = _close_now, _close_later
"""

_PROGRAM = r'''
import asyncio, json, multiprocessing, os, sys, threading, time
from pathlib import Path

SESSION = "scheduled-session"


def worker_lock():
    from openprogram.operations.worker.lock import WorkerLock
    lock = WorkerLock()
    assert lock.try_acquire()
    return lock


def session_directory():
    from openprogram.runtime.execution import default_store
    return default_store().tables.log.function_log_directory(SESSION).parent


def run_rows(path):
    from openprogram.state.store.session.session_log import RUN_ROWS_RECORD, read_log_file
    if not path.exists():
        return []
    return [line for record in read_log_file(path) if record.get("type") == RUN_ROWS_RECORD
            for line in record["data"]["rows"]]


def hold_a_turn(folder, ready):
    """A process that owns one turn, until told to finish it."""
    from types import SimpleNamespace
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry
    from openprogram.runtime.execution import default_store

    store = default_store()
    release = Path(folder) / "release"

    def work(*, request, cancel_event):
        from openprogram.runtime.execution.agent_journal import AgentJournal
        AgentJournal(SESSION, admission.execution_id).record_decision(
            {"role": "assistant", "content": [{"type": "text", "text": "looked"}]}, action_id="first", turn={})
        ready.send(admission.execution_id)
        while not release.exists():
            time.sleep(0.02)
        return SimpleNamespace(failed=False)

    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id=SESSION,
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "check", "agent_id": "main", "source": "scheduler"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )

    async def run():
        active = await entry.activate(admission)
        await driver._handles[(admission.execution_id, active.attempt_id, active.generation)].done

    asyncio.run(run())


def start_holder(folder):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=hold_a_turn, args=(folder, send))
    process.start()
    send.close()
    assert receive.poll(60), "the turn never started"
    return process, receive.recv()


def finish(folder, process):
    (Path(folder) / "release").write_text("go")
    process.join(60)
    assert process.exitcode == 0


def new_session(folder):
    from openprogram.runtime.context.session_db import default_db
    default_db().create_session(SESSION, "main", work_dir=str(folder))


def sub_agent(folder):
    import fake_model  # noqa: F401
    from openprogram.programs.tools.jobs.cron import cron, worker
    from openprogram.runtime.conversation import process_runner
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.permissions.authority import local_owner_authority

    lock = worker_lock()
    new_session(folder)
    runner = get_runner()
    process_runner._link_registration_modules = lambda: ["fake_model"]
    spec = cron._build_execution_spec(kind="prompt", body="check", cwd=str(folder), entry_id="hourly",
                                      authority=local_owner_authority(), origin=(SESSION, "main"))
    entry = {"id": "hourly", "title": "Hourly", "cron": "0 * * * *", "prompt": "check", "execution": spec}
    process = worker._spawn(entry, str(Path(folder) / "logs"))
    process.join(90)
    alive = process.is_alive()
    if alive:
        process.terminate()
        process.join(10)
    relay = next(t for t in threading.enumerate() if t.name == "cron-job-relay")
    relay.join(30)
    jobs = runner.list_jobs(SESSION, limit=10)
    store = default_store()
    directory = session_directory()
    child_rows = [line for path in (directory / "agents").glob("*.jsonl") for line in run_rows(path)]
    print(json.dumps({
        "alive": alive,
        "run_log": next((Path(folder) / "logs").glob("*.log")).read_text(),
        "jobs": [{"status": job.status.value, "result": job.result_text} for job in jobs],
        "job_modes": [store.get_job_agent_input(job.id)["turn_request"]["permission_mode"] for job in jobs],
        "conversation_mode": worker._conversation_settings(SESSION)[0],
        "child_job_rows": [line for line in child_rows if line.get("t") == "job_admissions"],
        "worker_job_rows": len([line for line in run_rows(directory / "main.jsonl") if line.get("t") == "job_admissions"]),
        "child_turns": len([line for line in child_rows if line.get("t") == "executions"]),
        "unfinished": [row.status.value for row in store.list_nonterminal(session_id=SESSION)],
    }))
    lock.release()


def restart_leaves_turn(folder):
    from openprogram.runtime.execution import default_control_service, default_store
    from openprogram.runtime.execution.restart import prepare_shutdown
    from openprogram.runtime.jobs import get_runner

    lock = worker_lock()
    new_session(folder)
    runner = get_runner()
    process, execution_id = start_holder(folder)
    store = default_store()
    store.tables.forget_session(SESSION)
    store.tables.ensure_session(SESSION)
    loaded = store.get_execution(execution_id)
    prepare_shutdown(runner)
    after_shutdown = store.get_execution(execution_id)
    recovered = default_control_service().recover_startup()
    after_startup = store.get_execution(execution_id)
    finish(folder, process)
    main_rows = run_rows(session_directory() / "main.jsonl")
    store.tables.take_up_other_process_turns(SESSION)
    print(json.dumps({
        "pausable": loaded.capabilities.pause,
        "loaded": loaded.status.value,
        "after_shutdown": after_shutdown.status.value,
        "after_startup": after_startup.status.value,
        "recovered": [item.execution.execution_id for item in recovered],
        "worker_wrote_turn": [line for line in main_rows if line.get("t") == "executions"
                              and line["r"]["execution_id"] == execution_id],
        "after_child": store.get_execution(execution_id).status.value,
    }))
    lock.release()


def new_worker_follows_a_surviving_turn(folder):
    """A new Worker finds a turn still running in a process that outlived the last one."""
    from openprogram.runtime.execution import default_control_service, default_store

    lock = worker_lock()
    new_session(folder)
    process, execution_id = start_holder(folder)
    store = default_store()
    store.tables.forget_session(SESSION)
    store.tables.ensure_session(SESSION)
    default_control_service().recover_startup()
    at_start = store.get_execution(execution_id).status.value
    finish(folder, process)
    deadline = time.monotonic() + 20
    while store.list_nonterminal(session_id=SESSION) and time.monotonic() < deadline:
        time.sleep(0.05)
    print(json.dumps({"at_start": at_start, "after_it_ended": store.get_execution(execution_id).status.value}))
    lock.release()


def leave_written_over(folder):
    """What the Worker used to do: pause a turn from a copy older than its end."""
    from openprogram.runtime.execution import default_control_service, default_store
    from openprogram.state.store.session.session_log import RUN_ROWS_RECORD

    lock = worker_lock()
    new_session(folder)
    process, execution_id = start_holder(folder)
    store = default_store()
    store.tables.forget_session(SESSION)
    store.tables.ensure_session(SESSION)
    stale = store.get_execution(execution_id)
    finish(folder, process)
    asyncio.run(default_control_service().request_pause(
        command_id=f"worker-restart:pause:{stale.current_attempt_id}", execution_id=execution_id,
        expected_version=stale.status_version, actor={"surface": "worker-restart"},
    ))
    print(json.dumps({"execution_id": execution_id, "status": store.get_execution(execution_id).status.value}))
    lock.release()


def recover_written_over(folder):
    import logging
    from openprogram.runtime.execution import default_control_service, default_store

    failures = []

    class Failures(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                failures.append(record.getMessage())

    logging.getLogger("openprogram.runtime.execution").addHandler(Failures())
    lock = worker_lock()
    execution_id = sys.argv[3]
    store = default_store()
    store.tables.ensure_session(SESSION)
    before = store.get_execution(execution_id)
    default_control_service().recover_startup()
    after = store.get_execution(execution_id)
    commands = [row["status"] for row in store.tables.handle.rows("commands", execution_id=execution_id)]
    second = default_control_service().recover_startup()
    print(json.dumps({
        "before": before.status.value,
        "after": after.status.value,
        "reason": after.reason_code,
        "owner": dict(after.owner_lease),
        "commands": commands,
        "failures": failures,
        "second_pass": len(second),
        "unfinished": len(store.list_nonterminal(session_id=SESSION)),
        "active": SESSION in store.tables.log.active_sessions(),
        "released": store.tables.forget_finished_sessions(-1, now=time.time() + 3600),
    }))
    lock.release()


def hold_a_scheduled_turn(ready):
    """A scheduled run's process, killed before its first model answer."""
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry
    from openprogram.runtime.execution import default_store

    store = default_store()

    def work(*, request, cancel_event):
        ready.send(admission.execution_id)
        time.sleep(600)

    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id=SESSION,
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "look again", "agent_id": "main", "source": "scheduler", "background": True,
            "advance_head": False, "scheduled_task": {"id": "hourly", "title": "Hourly"}}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="scheduled-u", assistant_message_id="scheduled-u_reply",
    )

    async def run():
        active = await entry.activate(admission)
        await driver._handles[(admission.execution_id, active.attempt_id, active.generation)].done

    asyncio.run(run())


def worker_continues_a_scheduled_run(folder):
    """The run's process dies with the Worker; the next Worker continues the turn to its end."""
    import fake_model  # noqa: F401
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_control_service, default_store
    from openprogram.runtime.execution.restart import reconcile
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.scheduler import task_runs

    lock = worker_lock()
    default_db().create_session(SESSION, "main", work_dir=str(folder), source="web")
    default_db().append_message(SESSION, {"id": "first", "role": "user", "content": "hi",
                                          "predecessor": None, "timestamp": 1.0})
    default_db().set_head(SESSION, "first")
    from openprogram.programs.tools.jobs.cron import cron
    cron._save(cron._resolve_path(), [{"id": "hourly", "title": "Hourly"}])
    runner = get_runner()
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=hold_a_scheduled_turn, args=(send,))
    process.start()
    send.close()
    assert receive.poll(60), "the turn never started"
    execution_id = receive.recv()
    process.kill()
    process.join(30)
    store = default_store()
    store.tables.forget_session(SESSION)
    store.tables.ensure_session(SESSION)
    default_control_service().recover_startup()
    paused = store.get_execution(execution_id)
    runs_while_paused = task_runs.recent("hourly")
    reconcile(runner)
    deadline = time.monotonic() + 60
    posted = []
    while time.monotonic() < deadline:
        posted = [row for row in default_db().get_messages(SESSION) if row.get("always_on_report")]
        if posted and store.get_execution(execution_id).status.value == "completed":
            break
        time.sleep(0.05)
    reconcile(runner)
    print(json.dumps({
        "paused": paused.status.value,
        "runs_while_paused": runs_while_paused,
        "status": store.get_execution(execution_id).status.value,
        "runs": [{"status": run["status"], "text": run["text"]} for run in task_runs.recent("hourly")],
        "posted": [row["content"] for row in posted],
    }), flush=True)
    lock.release()
    os._exit(0)


def wait_on_a_dead_worker(folder, notices, answers):
    """A run waiting for an answer from a Worker that has gone."""
    from openprogram.programs.tools.jobs.cron import worker
    from openprogram.runtime.execution.worker_calls import SubprocessWorkerCalls, use_worker_calls
    from openprogram.runtime.jobs.runner.shared import get_runner

    calls = SubprocessWorkerCalls(notices.put)
    use_worker_calls(calls)
    worker._listen_for_answers(notices, answers, calls, None)
    try:
        get_runner().get_job("some-job")
    except Exception as exc:
        (Path(folder) / "ended").write_text(f"{type(exc).__name__}: {exc}")


def worker_dies(folder):
    context = multiprocessing.get_context("spawn")
    notices, answers = context.Queue(), context.Queue()
    context.Process(target=wait_on_a_dead_worker, args=(folder, notices, answers)).start()
    assert notices.get(timeout=60)
    os._exit(0)


def start_a_task(folder, prompt, origin=None, loaded=False):
    """The Worker fires a task the way its tick does; returns the run's process.

    ``loaded``: the Worker already holds the run's conversation, as it does the
    chat a task was set in, or a task's own conversation after an earlier run."""
    import run_model  # noqa: F401
    from openprogram.programs.tools.jobs.cron import cron, worker
    from openprogram.runtime.conversation import process_runner
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.permissions.authority import local_owner_authority

    get_runner()
    process_runner._link_registration_modules = lambda: ["run_model"]
    spec = cron._build_execution_spec(kind="prompt", body=prompt, cwd=str(folder), entry_id="hourly",
                                      authority=local_owner_authority(), origin=origin)
    entry = {"id": "hourly", "title": "Hourly", "cron": "0 * * * *", "prompt": prompt, "execution": spec}
    cron._save(cron._resolve_path(), [entry])
    if loaded:
        default_store().tables.ensure_session(spec["session_id"])
    return worker._spawn(entry, str(Path(folder) / "logs")), spec["session_id"]


def wait_for(test, seconds=60):
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        time.sleep(0.05)
    return test()


def running_execution(folder):
    """The run's execution, learnt from the run itself: the Worker reads nothing in for it."""
    from openprogram.runtime.execution import default_store
    assert wait_for(lambda: (Path(folder) / "started").exists()), "the run never reached its wait"
    return default_store(), (Path(folder) / "execution").read_text()


def relay_done():
    relay = next((t for t in threading.enumerate() if t.name == "cron-job-relay"), None)
    if relay is not None:
        relay.join(30)


def closed(folder):
    path = Path(folder) / "closed"
    return path.read_text().split() if path.exists() else []


def stop_from_the_worker(folder):
    """The user stops a scheduled run in the App while its tool runs."""
    from openprogram.runtime.execution.run_control import cancel_execution
    from openprogram.runtime.scheduler import task_runs

    lock = worker_lock()
    process, _ = start_a_task(folder, sys.argv[3], loaded=sys.argv[4] == "loaded")
    store, execution_id = running_execution(folder)
    asked = time.monotonic()
    answer = cancel_execution(execution_id)
    process.join(30)
    took = time.monotonic() - asked
    alive = process.is_alive()
    if alive:
        process.terminate()
        process.join(10)
    relay_done()
    record = store.get_execution(execution_id)
    print(json.dumps({
        "answer": answer["status"], "alive": alive, "seconds": took, "exitcode": process.exitcode,
        "status": record.status.value, "slept": (Path(folder) / "slept").exists(),
        "closed": closed(folder), "execution_id": execution_id, "runs": task_runs.recent("hourly"),
    }))
    lock.release()


def kill_the_process(folder):
    """The run's process is killed; the Worker goes on."""
    from openprogram.runtime.scheduler import task_runs

    lock = worker_lock()
    process, session_id = start_a_task(folder, "wait for the model", loaded=sys.argv[3] == "loaded")
    store, execution_id = running_execution(folder)
    os.kill(process.pid, 9)
    process.join(30)
    relay_done()
    wait_for(lambda: closed(folder), 10)
    record = store.get_execution(execution_id)
    print(json.dumps({
        "status": record.status.value, "reason": record.reason_code, "owner": dict(record.owner_lease),
        "closed": closed(folder), "execution_id": execution_id,
        "runs": [(run["status"], run["text"]) for run in task_runs.recent("hourly")],
        "unfinished": [row.status.value for row in store.list_nonterminal(session_id=session_id)],
    }))
    lock.release()


def report_in_its_own_conversation(folder):
    """A task set outside any chat reports into the conversation its runs make."""
    from openprogram.runtime.context.session_db import default_db

    lock = worker_lock()
    process, session_id = start_a_task(folder, "check")
    process.join(90)
    relay_done()
    db = default_db()
    posted = wait_for(lambda: [row for row in db.get_branch(session_id) if row.get("always_on_report")], 30)
    print(json.dumps({
        "session_id": session_id, "head": (db.get_session(session_id) or {}).get("head_id"),
        "main_line": [(row["role"], row.get("content")) for row in db.get_branch(session_id)],
        "posted": [row["content"] for row in posted or []],
    }), flush=True)
    lock.release()
    os._exit(0)


if __name__ == "__main__":
    {"sub_agent": sub_agent, "worker_dies": worker_dies, "restart_leaves_turn": restart_leaves_turn,
     "new_worker_follows_a_surviving_turn": new_worker_follows_a_surviving_turn,
     "leave_written_over": leave_written_over, "recover_written_over": recover_written_over,
     "worker_continues_a_scheduled_run": worker_continues_a_scheduled_run,
     "stop_from_the_worker": stop_from_the_worker, "kill_the_process": kill_the_process,
     "report_in_its_own_conversation": report_in_its_own_conversation}[sys.argv[1]](sys.argv[2])
'''


def _run(tmp_path, stage, *arguments):
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "fake_model.py").write_text(_FAKE_MODEL)
    (tmp_path / "run_model.py").write_text(_RUN_MODEL)
    program = tmp_path / "program.py"
    program.write_text(_PROGRAM)
    root = Path(__file__).resolve().parents[3]
    finished = subprocess.run(
        [sys.executable, str(program), stage, str(folder), *arguments], text=True, capture_output=True, timeout=180,
        env={**os.environ, "HOME": str(tmp_path / "home"), "OPENPROGRAM_PROFILE": "", "RUN_FOLDER": str(folder),
             "PYTHONPATH": os.pathsep.join((str(root), str(tmp_path)))},
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    return json.loads(finished.stdout.strip().splitlines()[-1]) if finished.stdout.strip() else None


def test_a_scheduled_tasks_sub_agent_runs_in_the_worker(tmp_path):
    report = _run(tmp_path, "sub_agent")
    assert not report["alive"], report["run_log"]
    assert report["jobs"] == [{"status": "completed", "result": "the screen is clear"}]
    assert "sub-agent said: the screen is clear" in report["run_log"]
    # The Job was admitted where it could be claimed; the turn stayed its process's own.
    assert report["child_job_rows"] == [] and report["worker_job_rows"] > 0
    assert report["child_turns"] > 0
    assert report["job_modes"] == [report["conversation_mode"]]
    # The Worker read back how the run ended.
    assert report["unfinished"] == []


def test_a_restart_leaves_a_turn_another_process_owns(tmp_path):
    report = _run(tmp_path, "restart_leaves_turn")
    assert report["pausable"] and report["loaded"] == "running"
    assert report["after_shutdown"] == "running" and report["after_startup"] == "running"
    assert report["recovered"] == [] and report["worker_wrote_turn"] == []
    assert report["after_child"] == "completed"


def test_a_new_worker_sees_how_a_surviving_turn_ended(tmp_path):
    report = _run(tmp_path, "new_worker_follows_a_surviving_turn")
    assert report == {"at_start": "running", "after_it_ended": "completed"}


def test_a_turn_written_over_after_it_ended_recovers_its_ending(tmp_path):
    written = _run(tmp_path, "leave_written_over")
    assert written["status"] == "pausing"
    report = _run(tmp_path, "recover_written_over", written["execution_id"])
    assert report["before"] == "pausing" and report["failures"] == []
    assert report["after"] == "completed" and report["owner"] == {}
    assert report["commands"] == ["rejected"] and report["second_pass"] == 0
    assert report["unfinished"] == 0 and not report["active"] and report["released"] == 1


def test_a_scheduled_run_the_next_worker_continues_ends_as_that_run(tmp_path):
    report = _run(tmp_path, "worker_continues_a_scheduled_run")
    assert report["paused"] == "paused" and report["runs_while_paused"] == []
    assert report["status"] == "completed"
    assert report["runs"] == [{"status": "result", "text": "the screen is clear"}]
    assert report["posted"] == ["the screen is clear"]


def test_a_run_waiting_on_the_worker_ends_when_the_worker_is_gone(tmp_path):
    _run(tmp_path, "worker_dies")
    ended = tmp_path / "work" / "ended"
    deadline = time.monotonic() + 30
    while not ended.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ended.read_text() == "WorkerUnavailable: the Worker that started this scheduled run has stopped"


@pytest.mark.parametrize("held", ["loaded", "not loaded"])
@pytest.mark.parametrize("wait", ["wait in a tool", "wait for the model"])
def test_a_stop_asked_in_the_worker_ends_a_run_in_its_own_process(tmp_path, wait, held):
    report = _run(tmp_path, "stop_from_the_worker", wait, held)
    assert report["answer"] == "cancelling" and not report["alive"] and report["exitcode"] == 0
    assert report["seconds"] < 10, report
    assert report["status"] == "cancelled" and not report["slept"]
    # Its pages closed before the process exited, not on a thread the exit could cut off.
    assert report["closed"][:1] == [report["execution_id"]]
    # A run the user stopped is no news and no failure.
    assert report["runs"] == []


@pytest.mark.parametrize("held", ["loaded", "not loaded"])
def test_a_killed_run_process_is_ended_by_the_worker_at_once(tmp_path, held):
    report = _run(tmp_path, "kill_the_process", held)
    assert report["status"] == "interrupted" and report["owner"] == {} and report["unfinished"] == []
    assert report["closed"] == [f"later:{report['execution_id']}"]
    assert report["runs"] == [["failed", "the run was interrupted and could not continue"]]


def test_a_task_set_outside_a_chat_reports_into_its_own_conversation(tmp_path):
    report = _run(tmp_path, "report_in_its_own_conversation")
    assert report["session_id"] == "cron-hourly"
    assert report["posted"] == ["the screen is clear"]
    assert report["main_line"] == [["assistant", "the screen is clear"]]
