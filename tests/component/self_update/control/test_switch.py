"""Every row of the design's failure table, on a real store and pointer.

The fake host stands in for what would touch this machine; the phases, the
pointer, the journal and the maintenance marker are the real ones.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update.control import switch, watchdog
from openprogram.operations.self_update.control.maintenance import load_maintenance, maintenance_blocks
from openprogram.operations.self_update.control.switch import CommitIncomplete, Failed, RebaseConflict, SwitchController
from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.operations.self_update.versions.layout import Layout

OLD, NEW = "old-version", "new-version"
BASE = "py3.12-" + "a" * 16
BASE_SHA, CANDIDATE_SHA = "1" * 40, "2" * 40
UPDATE = "su_switch"


class Died(BaseException):
    """The controller process ends here; nothing in it runs afterwards."""


class Host:
    """Everything passes unless a test says otherwise."""

    def __init__(self, layout: Layout) -> None:
        self.layout, self.calls, self.fail, self.tip, self.pid = layout, [], {}, BASE_SHA, 321

    def _do(self, name: str):
        self.calls.append(name)
        outcome = self.fail.get(name)
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, BaseException):
            raise outcome

    def local_tip(self):
        return self.tip

    def is_based_on(self, record, commit, base):
        return True

    def rebase(self, record, onto):
        self._do("rebase")
        return "3" * 40

    def build(self, record, commit, parent):
        self._do("build")
        return NEW

    def trial(self, record, version):
        self._do("trial")
        return {"isolation": "sandbox", "checks": ["data_copy"]}

    def wait_idle(self, until):
        self._do("wait_idle")
        return "wait_idle" not in self.fail

    def request_checkpoint(self, record):
        self._do("request_checkpoint")

    def probe_old(self, record):
        self._do("probe_old")
        return {"gate": {"candidate_sha": BASE_SHA}, "subsystems": {}}

    def stop_worker(self):
        self._do("stop_worker")

    def start_worker(self):
        self._do("start_worker")

    def snapshot_databases(self, update_id):
        self._do("snapshot_databases")
        return "/backups/before-switch"

    def is_up(self, record, commit):
        self._do("is_up")
        return self.pid

    def migrating(self, version):
        return False

    def probe_candidate(self, record, version, commit, reference):
        self._do("probe_candidate")

    def turn_check(self, record):
        self._do("turn_check")
        return {"outcome": "ok"}

    def probation(self, record, version, commit, pid, reference, until):
        self._do("probation")

    def shell_handshake(self, record, version):
        self._do("shell_handshake")

    def move_local(self, commit, expected, version):
        self._do("move_local")

    def backup_repo(self):
        self._do("backup_repo")

    def probe_restored(self, record, revision, reference):
        self._do("probe_restored")
        return {"candidate_sha": BASE_SHA}

    def retention(self):
        self._do("retention")

    def note_recovery(self, line):
        self.calls.append("note_recovery")
        self.recovery_line = line


@dataclass
class Machine:
    store: SelfUpdateStore
    layout: Layout
    host: Host

    def drive(self, host: Host | None = None) -> None:
        SwitchController(self.store, UPDATE, host or self.host, self.layout).advance()

    def resumed_host(self) -> Host:
        self.host = Host(self.layout)
        return self.host

    @property
    def phase(self) -> UpdatePhase:
        return self.store.load(UPDATE).state.phase

    @property
    def detail(self) -> dict:
        return self.store.load(UPDATE).state.detail

    @property
    def pointer(self) -> pointers.Pointer:
        return pointers.read(self.layout)

    def journal(self) -> dict:
        return json.loads((self.store.root / UPDATE / switch.JOURNAL).read_text())

    def admission_closed(self) -> bool:
        return load_maintenance(self.store) is not None

    def guard_returns(self) -> None:
        """What the launcher's guard does when the candidate keeps crashing."""
        pointers.rollback(self.layout, NEW, deadline=time.time() + 1, writer="launcher")

    def assert_nothing_left_closed(self) -> None:
        assert not self.admission_closed() and not (self.layout.root / "switching").exists()


def _seal(layout: Layout, version: str, **fields) -> None:
    directory = layout.version(version)
    directory.mkdir(parents=True)
    (directory / "version.json").write_text(json.dumps({"id": version, "base": BASE, "components": ["runtime"], **fields}))


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    from openprogram.state import paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    layout = Layout(state / "runtime")
    layout.root.mkdir(parents=True)
    _seal(layout, OLD)
    _seal(layout, NEW)
    pointers.create(layout, pointers.Pointer(generation=1, version=OLD, base=BASE, last_good=OLD, last_good_base=BASE,
                                             writer="test"))
    store = SelfUpdateStore()
    store.create(UpdateRequest(
        update_id=UPDATE, session_id="session-1", origin_turn_id="turn-1", origin_assistant_id="turn-1_reply",
        agent_id="main", repo=str(layout.repo), worktree_id="wt_task", base_sha=BASE_SHA, candidate_sha=CANDIDATE_SHA,
        changed_paths=("openprogram/feature.py",), pre_update_evidence=("git-status:clean",), goal="Add behaviour",
        assertions=("It works",), controller_version=OLD, task="feature", kind="change",
    ))
    store.transition(UPDATE, UpdatePhase.BUILDING)
    return Machine(store, layout, Host(layout))


# Without a failure -------------------------------------------------------------


def test_a_good_update_goes_through_every_phase_and_commits(machine: Machine) -> None:
    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    current = machine.pointer
    assert (current.version, current.last_good, current.previous_good, current.probation) == (NEW, NEW, OLD, "")
    assert machine.detail["candidate_version"] == NEW and machine.detail["turn_check"] == {"outcome": "ok"}
    assert machine.host.calls.index("stop_worker") < machine.host.calls.index("start_worker")
    assert "move_local" in machine.host.calls
    machine.assert_nothing_left_closed()


def test_admission_closes_only_around_the_pointer_move(machine: Machine) -> None:
    seen: dict[str, bool] = {}
    machine.host.fail = {
        "probe_old": lambda: seen.setdefault("before", machine.admission_closed()) and None,
        "stop_worker": lambda: seen.setdefault("during", machine.admission_closed()) and None,
        "probation": lambda: seen.setdefault("probation", machine.admission_closed()) and None,
    }

    machine.drive()

    assert seen == {"before": False, "during": True, "probation": False}


def test_local_moved_during_the_trial_rebases_and_builds_again(machine: Machine) -> None:
    def moved() -> None:
        machine.host.tip = "9" * 40
        machine.host.fail.pop("trial")

    machine.host.fail["trial"] = moved

    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    assert machine.detail["rebases"] == 1 and machine.detail["landed_sha"] == "3" * 40
    assert machine.host.calls.count("build") == 2 and machine.host.calls.count("trial") == 2


def test_a_destructive_migration_is_backed_up_and_named_after_rollback(machine: Machine) -> None:
    (machine.layout.version(NEW) / "version.json").write_text(json.dumps(
        {"id": NEW, "base": BASE, "components": ["runtime"], "destructive_migration": "drop column"}))
    machine.host.fail["turn_check"] = Failed("the turn failed")

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK
    assert machine.detail["backup"] == "/backups/before-switch"


# The failure table -------------------------------------------------------------


@pytest.mark.parametrize("error", ["compile error", "network unreachable", "no space left on device"])
def test_build_failure_aborts_and_keeps_the_task(machine: Machine, error: str) -> None:
    work = machine.layout.work / "feature"
    work.mkdir(parents=True)
    machine.host.fail["build"] = RuntimeError(error)

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and error in machine.detail["error"]
    assert work.is_dir() and "stop_worker" not in machine.host.calls
    assert machine.pointer.version == OLD


def test_rebase_conflict_aborts_and_names_the_files(machine: Machine) -> None:
    machine.host.is_based_on = lambda *_: False
    machine.host.fail["rebase"] = RebaseConflict("does not rebase cleanly", ("a.py", "b.py"))

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and machine.detail["files"] == ["a.py", "b.py"]


def test_trial_failure_aborts_keeps_the_version_and_asks_for_diagnosis(machine: Machine) -> None:
    machine.host.fail["trial"] = RuntimeError("verifier rejected the update")

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and machine.detail["diagnose"] is True
    assert machine.layout.version(NEW).is_dir() and machine.pointer.version == OLD
    assert "stop_worker" not in machine.host.calls


def test_waiting_for_tasks_past_the_limit_aborts(machine: Machine, monkeypatch) -> None:
    monkeypatch.setattr(switch, "QUIET_SECONDS", 0)
    monkeypatch.setattr(switch, "QUIESCE_LIMIT_SECONDS", 0)
    machine.host.fail["wait_idle"] = None

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and "still running" in machine.detail["error"]
    assert "request_checkpoint" in machine.host.calls and "stop_worker" not in machine.host.calls
    machine.assert_nothing_left_closed()


def test_cancel_while_waiting_for_tasks_stops_the_update(machine: Machine) -> None:
    def cancelled() -> None:
        machine.store.transition(UPDATE, UpdatePhase.ABORTED, expected_phase=UpdatePhase.QUIESCING,
                                 detail={"reason": "cancelled"})

    machine.host.fail["wait_idle"] = cancelled

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and machine.detail == {"reason": "cancelled"}
    assert "stop_worker" not in machine.host.calls


def test_old_version_failing_its_probe_before_the_switch_aborts(machine: Machine) -> None:
    machine.host.fail["probe_old"] = RuntimeError("healthz refused")

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and machine.pointer.version == OLD
    assert "stop_worker" not in machine.host.calls


def test_controller_dies_before_the_pointer_moves_with_the_worker_up_resumes(machine: Machine) -> None:
    machine.host.fail["request_checkpoint"] = Died()
    with pytest.raises(Died):
        machine.drive()
    assert machine.phase is UpdatePhase.ACTIVATING and machine.pointer.version == OLD

    host = machine.resumed_host()
    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    assert host.calls.index("stop_worker") < host.calls.index("start_worker")


def test_maintenance_expires_when_nothing_resumes(machine: Machine) -> None:
    machine.host.fail["request_checkpoint"] = Died()
    with pytest.raises(Died):
        machine.drive()
    assert maintenance_blocks("web") is True

    marker = machine.store.root / "maintenance.json"
    marker.write_text(json.dumps({**json.loads(marker.read_text()), "deadline": time.time() - 1}))

    assert maintenance_blocks("web") is False


def test_controller_dies_after_stopping_the_worker_restarts_the_old_one(machine: Machine) -> None:
    machine.host.fail["stop_worker"] = Died()
    with pytest.raises(Died):
        machine.drive()
    assert machine.journal()["stopped"] is True and machine.pointer.version == OLD

    host = machine.resumed_host()
    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    assert host.calls.index("start_worker") < host.calls.index("request_checkpoint")


def test_pointer_write_failure_restarts_the_old_version_and_aborts(machine: Machine, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise pointers.PointerError("disk full")

    monkeypatch.setattr(pointers, "activate", refuse)

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and "could not be moved" in machine.detail["error"]
    assert machine.pointer.version == OLD and machine.host.calls[-1] == "start_worker"
    machine.assert_nothing_left_closed()


def test_candidate_that_does_not_start_is_rolled_back(machine: Machine, monkeypatch) -> None:
    monkeypatch.setattr(switch, "START_SECONDS", 0)
    machine.host.pid = None

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.pointer.version == OLD
    assert "probe_restored" in machine.host.calls
    machine.assert_nothing_left_closed()


def test_candidate_that_keeps_crashing_is_recorded_at_the_next_worker_start(machine: Machine, monkeypatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(switch, "launch", lambda update_id, store=None: launched.append(update_id))
    machine.host.fail["is_up"] = Died()
    with pytest.raises(Died):
        machine.drive()
    machine.guard_returns()

    switch.reconcile_at_start(machine.store, machine.store.load(UPDATE))

    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.detail["rollback"] == "launcher"
    assert launched == [] and not machine.admission_closed()


@pytest.mark.parametrize("step", ["probe_candidate", "turn_check", "probation", "shell_handshake"])
def test_failure_after_the_switch_goes_back(machine: Machine, step: str) -> None:
    (machine.layout.version(NEW) / "version.json").write_text(json.dumps(
        {"id": NEW, "base": BASE, "components": ["runtime", "desktop"]}))
    machine.host.fail[step] = Failed(f"{step} regressed")

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK and step in machine.detail["error"]
    current = machine.pointer
    assert (current.version, current.probation) == (OLD, "") and machine.detail["rollback"] == "moved"
    assert machine.host.calls.index("probe_restored") > machine.host.calls.index("stop_worker")
    assert "move_local" not in machine.host.calls
    machine.assert_nothing_left_closed()


def test_rollback_never_touches_user_data(machine: Machine, tmp_path: Path) -> None:
    row = tmp_path / "state" / "user.sqlite"
    row.write_text("the owner's data")
    machine.host.fail["probe_candidate"] = Failed("regressed")

    machine.drive()

    assert row.read_text() == "the owner's data"


def test_controller_dies_after_the_pointer_moved_with_a_healthy_candidate(machine: Machine, monkeypatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(switch, "launch", lambda update_id, store=None: launched.append(update_id))
    monkeypatch.setattr("openprogram.operations.self_update.repair.diagnosis.dispatch_pending", lambda: None)
    machine.host.fail["probe_candidate"] = Died()
    with pytest.raises(Died):
        machine.drive()
    assert machine.phase is UpdatePhase.VERIFYING and machine.pointer.version == NEW

    watchdog.tick(machine.store)  # No heartbeat was ever written, so it is stale.
    assert launched == [UPDATE]

    machine.resumed_host()
    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED and machine.pointer.probation == ""


def test_a_fresh_heartbeat_starts_no_second_controller(machine: Machine, monkeypatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(switch, "launch", lambda update_id, store=None: launched.append(update_id))
    monkeypatch.setattr("openprogram.operations.self_update.repair.diagnosis.dispatch_pending", lambda: None)
    (machine.store.root / UPDATE / switch.HEARTBEAT).write_text("1\n")

    watchdog.tick(machine.store)

    assert launched == []


def test_controller_dies_after_the_pointer_moved_with_an_unhealthy_candidate(machine: Machine, monkeypatch) -> None:
    monkeypatch.setattr(switch, "take_next", lambda store: None)
    machine.host.fail["probe_candidate"] = Died()
    with pytest.raises(Died):
        machine.drive()
    machine.guard_returns()

    switch.reconcile_at_start(machine.store, machine.store.load(UPDATE))

    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.pointer.version == OLD
    machine.assert_nothing_left_closed()


def test_verifying_past_its_limit_goes_back(machine: Machine, monkeypatch) -> None:
    monkeypatch.setattr(switch, "VERIFY_LIMIT_SECONDS", 0)

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK and "time limit" in machine.detail["error"]


def test_probation_deadline_passing_the_launcher_goes_back(machine: Machine) -> None:
    machine.host.fail["probation"] = machine.guard_returns

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.detail["rollback"] == "already"
    assert "move_local" not in machine.host.calls


def test_reboot_during_verifying_continues(machine: Machine) -> None:
    machine.host.fail["turn_check"] = Died()
    with pytest.raises(Died):
        machine.drive()
    host = machine.resumed_host()

    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    assert "probe_candidate" not in host.calls and "turn_check" in host.calls


def test_reboot_during_verifying_can_end_with_the_guard_returning(machine: Machine, monkeypatch) -> None:
    monkeypatch.setattr(switch, "take_next", lambda store: None)
    machine.host.fail["turn_check"] = Died()
    with pytest.raises(Died):
        machine.drive()
    machine.guard_returns()

    switch.reconcile_at_start(machine.store, machine.store.load(UPDATE))

    assert machine.phase is UpdatePhase.ROLLED_BACK


@pytest.mark.parametrize("outcome", ["already", "pending"])
def test_rollback_pointer_conflict_or_failure_still_ends_rolled_back(machine: Machine, monkeypatch, outcome: str) -> None:
    monkeypatch.setattr(pointers, "rollback", lambda *a, **k: outcome)
    machine.host.fail["probe_candidate"] = Failed("regressed")

    machine.drive()

    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.detail["rollback"] == outcome


def test_a_real_pending_rollback_is_journaled_for_the_launcher(machine: Machine, monkeypatch) -> None:
    from openprogram.operations.self_update.versions import pointer as real

    monkeypatch.setattr(switch, "ROLLBACK_WRITE_SECONDS", 0)
    machine.host.fail["probe_candidate"] = Failed("regressed")
    original = real.compare_and_set

    def refuse(layout, generation, *, writer, **changes):
        if writer == "controller" and changes.get("version") == OLD:
            raise real.PointerError("locked")
        return original(layout, generation, writer=writer, **changes)

    monkeypatch.setattr(real, "compare_and_set", refuse)

    machine.drive()

    assert machine.detail["rollback"] == "pending" and machine.layout.pending_rollback.exists()


@pytest.mark.parametrize("step", ["move_local", "backup_repo"])
def test_commit_step_failure_retries_on_resume_and_succeeds(machine: Machine, step: str) -> None:
    machine.host.fail[step] = RuntimeError("the repository is locked")
    with pytest.raises(CommitIncomplete):
        machine.drive()
    assert machine.phase is UpdatePhase.VERIFYING and machine.pointer.probation == ""
    assert machine.journal()["committing"] is True

    host = machine.resumed_host()
    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED and machine.pointer.version == NEW
    assert "probation" not in host.calls


def test_old_version_failing_after_rollback_needs_manual_recovery(machine: Machine) -> None:
    machine.host.fail["probe_candidate"] = Failed("regressed")
    machine.host.fail["probe_restored"] = RuntimeError("the old version does not answer")

    machine.drive()

    assert machine.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY
    assert "old version does not answer" in machine.host.recovery_line
    assert machine.pointer.version == OLD
    machine.assert_nothing_left_closed()


def test_three_moves_of_local_give_up(machine: Machine) -> None:
    def moved() -> None:
        machine.host.tip = f"{machine.host.calls.count('trial'):040d}"

    machine.host.fail["trial"] = moved

    machine.drive()

    assert machine.phase is UpdatePhase.ABORTED and "rebased three times" in machine.detail["error"]


def test_the_supervisor_entry_runs_the_switch_for_a_versions_update(monkeypatch) -> None:
    from openprogram.operations.self_update.control import supervisor

    seen: list[str] = []
    monkeypatch.setattr(switch, "run", lambda update_id: seen.append(update_id) or 0)

    assert supervisor.main([UPDATE]) == 0
    assert seen == [UPDATE]


def test_undo_to_commits_by_saving_the_old_tip(machine: Machine) -> None:
    calls = []
    machine.host.move_local = lambda *a: calls.append(("move", a))
    machine.host.go_back_local = lambda *a: calls.append(("back", a))
    original = machine.store.load
    def as_undo(update_id):
        from dataclasses import replace
        record = original(update_id)
        return replace(record, request=replace(record.request, kind="undo_to"))
    machine.store.load = as_undo
    machine.drive()
    assert machine.phase is UpdatePhase.SUCCEEDED and [name for name, _ in calls] == ["back"]


def test_advance_can_stop_at_a_phase(machine: Machine) -> None:
    SwitchController(machine.store, UPDATE, machine.host, machine.layout).advance(until=UpdatePhase.READY)
    assert machine.phase is UpdatePhase.READY
    assert machine.pointer.version == OLD


def _live_host(machine: Machine, *, shell: bool) -> switch.LiveHost:
    import os

    host = switch.LiveHost(machine.store, machine.layout)
    machine.layout.boot.mkdir(parents=True, exist_ok=True)
    if shell:
        (machine.layout.root / switch.SHELL_RECORD).write_text(
            json.dumps({"pid": os.getpid(), "executable": "/shell", "shell": "1.0.0"}))
    return host


def test_a_candidate_whose_desktop_failed_is_refused(machine: Machine) -> None:
    host = _live_host(machine, shell=False)
    (machine.layout.boot / f"{NEW}.desktop-failed").write_text("{}")
    with pytest.raises(Failed):
        host.shell_handshake(machine.store.load(UPDATE), NEW)


def test_with_no_shell_open_there_is_nothing_to_relaunch(machine: Machine) -> None:
    _live_host(machine, shell=False).shell_handshake(machine.store.load(UPDATE), NEW)
    assert not (machine.layout.root / switch.SHELL_RELAUNCH).exists()


def test_the_open_shell_is_asked_to_relaunch_and_must_report_both_handshakes(machine: Machine) -> None:
    import threading

    host = _live_host(machine, shell=True)
    ready = machine.layout.boot / f"{NEW}.desktop-ready"
    request = machine.layout.root / switch.SHELL_RELAUNCH
    ready.write_text(json.dumps({"main_ready": True, "window_loaded": True}))  # From an earlier start.

    def shell() -> None:
        while not request.exists():
            time.sleep(0.05)
        ready.write_text(json.dumps({"main_ready": True, "window_loaded": True}))

    thread = threading.Thread(target=shell, daemon=True)
    thread.start()
    host.shell_handshake(machine.store.load(UPDATE), NEW)
    thread.join(5)
    assert not request.exists()


def test_a_shell_that_never_reports_fails_the_switch(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(switch, "SHELL_HANDSHAKE_SECONDS", 0.2)
    host = _live_host(machine, shell=True)
    with pytest.raises(Failed, match="did not load"):
        host.shell_handshake(machine.store.load(UPDATE), NEW)
    assert not (machine.layout.root / switch.SHELL_RELAUNCH).exists()


def test_a_trial_starts_no_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    import openprogram.operations.self_update.delivery.controller as controller

    monkeypatch.setenv("OPENPROGRAM_TRIAL", "1")
    monkeypatch.setattr(controller, "launch_controller", lambda *a, **k: pytest.fail("a controller started"))
    switch.launch(UPDATE)


def test_a_worker_that_has_not_written_its_access_file_is_still_starting(tmp_path):
    """The switch stops the old worker, so right after the start nothing says how to reach the new one."""
    store = SelfUpdateStore(tmp_path / "state" / "self-update")
    host = switch.LiveHost(store, Layout(tmp_path / "state" / "runtime"))
    assert host.is_up(None, "0" * 40) is None


# A controller that dies at the very end ------------------------------------------


def _finished_with_admission_still_closed(machine: Machine) -> None:
    machine.host.fail["turn_check"] = Failed("the turn failed")
    real = switch.SwitchController._reopen_admission
    switch.SwitchController._reopen_admission = lambda self: (_ for _ in ()).throw(Died())
    try:
        with pytest.raises(Died):
            machine.drive()
    finally:
        switch.SwitchController._reopen_admission = real
    assert machine.phase is UpdatePhase.ROLLED_BACK and machine.admission_closed()


def test_a_finished_update_does_not_keep_admission_closed(machine: Machine) -> None:
    _finished_with_admission_still_closed(machine)
    assert switch.clear_finished_admission(machine.store, machine.layout)
    machine.assert_nothing_left_closed()


def test_the_watchdog_clears_what_a_dead_controller_left(machine: Machine, monkeypatch) -> None:
    from openprogram.operations.self_update.repair import diagnosis

    monkeypatch.setattr(diagnosis, "dispatch_pending", lambda: None)
    _finished_with_admission_still_closed(machine)
    watchdog.tick(machine.store)
    machine.assert_nothing_left_closed()


def test_a_new_update_is_not_refused_by_a_finished_updates_marker(machine: Machine) -> None:
    from dataclasses import replace

    _finished_with_admission_still_closed(machine)
    request = replace(machine.store.load(UPDATE).request, update_id="su_after")
    machine.store.create(request)
    assert machine.store.load("su_after").state.phase is UpdatePhase.PREPARING


def test_a_running_updates_marker_still_holds_the_slot(machine: Machine) -> None:
    from dataclasses import replace
    from openprogram.operations.self_update.control.maintenance import enter_maintenance
    from openprogram.operations.self_update.types import ActiveUpdateError

    for phase in (UpdatePhase.TRIAL, UpdatePhase.READY, UpdatePhase.QUIESCING, UpdatePhase.ACTIVATING):
        machine.store.transition(UPDATE, phase)
    enter_maintenance(UPDATE, deadline=time.time() + 300)
    assert not switch.clear_finished_admission(machine.store, machine.layout)
    with pytest.raises(ActiveUpdateError):
        machine.store.create(replace(machine.store.load(UPDATE).request, update_id="su_after"))


# A rebased update checks the commit it built --------------------------------------


def test_a_rebase_rebinds_the_request_and_every_frozen_config(machine: Machine) -> None:
    from openprogram.operations.self_update.repair.diagnosis import config_evidence as diagnosis_evidence
    from openprogram.operations.self_update.verification.verifier_config import config_evidence as verifier_evidence

    directory = machine.store.root / UPDATE
    schema = {"schema": {"properties": {"candidate_sha": {"type": "string", "const": CANDIDATE_SHA}}}}
    verifier, diagnosis = {"response_format": schema}, {"schema": 1, "response_format": schema}
    (directory / "verifier-config.json").write_text(json.dumps(verifier))
    (directory / "diagnosis-config.json").write_text(json.dumps(diagnosis))
    request = json.loads((directory / "request.json").read_text())
    request["pre_update_evidence"] = ["git-status:clean", verifier_evidence(verifier), diagnosis_evidence(diagnosis)]
    (directory / "request.json").write_text(json.dumps(request))

    machine.store.rebind_candidate(UPDATE, "3" * 40)
    machine.store.rebind_candidate(UPDATE, "3" * 40)  # A resumed controller repeats it.

    record = machine.store.load(UPDATE)
    rebound = json.loads((directory / "verifier-config.json").read_text())
    assert record.request.candidate_sha == "3" * 40
    assert rebound["response_format"]["schema"]["properties"]["candidate_sha"]["const"] == "3" * 40
    assert verifier_evidence(rebound) in record.request.pre_update_evidence
    assert verifier_evidence(verifier) not in record.request.pre_update_evidence
    assert len(record.request.pre_update_evidence) == 3


def test_the_trial_after_a_rebase_is_told_the_rebased_commit(machine: Machine) -> None:
    seen: list[str] = []
    real_trial = machine.host.trial

    def trial(record, version):
        seen.append(record.request.candidate_sha)
        if len(seen) == 1:
            machine.host.tip = "9" * 40
        return real_trial(record, version)

    machine.host.trial = trial

    machine.drive()

    assert machine.phase is UpdatePhase.SUCCEEDED
    assert seen == [CANDIDATE_SHA, "3" * 40]


def test_the_verifier_probes_the_commit_the_request_now_names(machine: Machine, monkeypatch) -> None:
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial import verifier

    machine.store.rebind_candidate(UPDATE, "3" * 40)
    revisions: list[str] = []
    monkeypatch.setattr(verifier, "probe_instance", lambda record, target, *, revision, **_: (
        revisions.append(revision), (_ for _ in ()).throw(RuntimeError("stop here")))[0])
    instance = SimpleNamespace(target=lambda baseline: SimpleNamespace(port=1, state_dir="/x"))

    with pytest.raises(RuntimeError, match="stop here"):
        verifier.run_verifier(machine.store, machine.store.load(UPDATE), instance, live=None, host_pid=1)

    assert revisions == ["3" * 40]


# Snapshots, environment ------------------------------------------------------------


def test_the_snapshot_before_a_destructive_migration_holds_the_conversations(machine: Machine) -> None:
    state = machine.layout.root.parent
    chat = state / "projects" / "p" / "chats" / "s"
    chat.mkdir(parents=True)
    (chat / "main.jsonl").write_text('{"type":"message"}\n')
    (state / "memory").mkdir()
    (state / "memory" / "note.md").write_text("remember\n")
    (state / "config.json").write_text("{}")
    (state / "revisions.jsonl").write_text('{"rows":[]}\n')
    (state / "run_index.jsonl").write_text('{"e":"x","s":"s"}\n')

    backup = Path(switch.LiveHost(machine.store, machine.layout).snapshot_databases(UPDATE))

    assert (backup / "projects" / "p" / "chats" / "s" / "main.jsonl").read_text() == '{"type":"message"}\n'
    assert (backup / "revisions.jsonl").is_file() and (backup / "run_index.jsonl").is_file()
    assert (backup / "memory" / "note.md").read_text() == "remember\n"
    assert (backup / "config.json").is_file()


def test_a_resumed_switch_repeats_the_snapshot_without_nesting_it(machine: Machine) -> None:
    state = machine.layout.root.parent
    for folder in ("projects", "sessions"):
        (state / folder).mkdir()
        (state / folder / "log.jsonl").write_text("first\n")
    host = switch.LiveHost(machine.store, machine.layout)
    host.snapshot_databases(UPDATE)
    (state / "projects" / "log.jsonl").write_text("second\n")

    backup = Path(host.snapshot_databases(UPDATE))

    assert (backup / "projects" / "log.jsonl").read_text() == "second\n"
    assert (backup / "sessions" / "log.jsonl").read_text() == "first\n"
    assert not (backup / "projects" / "projects").exists()


def test_a_worker_started_directly_gets_the_environment_the_last_one_had(machine: Machine, monkeypatch) -> None:
    from openprogram.operations.self_update.delivery import controller
    from openprogram.operations.worker import lifecycle

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-owner")
    monkeypatch.setenv("PYTHONHOME", "/not-for-a-worker")
    controller.save_worker_environment(store=machine.store)
    assert (machine.store.root / controller.WORKER_ENVIRONMENT).stat().st_mode & 0o077 == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    seen = {}
    monkeypatch.setattr(lifecycle, "spawn_detached", lambda **kw: seen.update(kw) or 0)
    switch.LiveHost(machine.store, machine.layout).start_worker()

    assert seen["environment"]["ANTHROPIC_API_KEY"] == "from-the-owner"
    saved = json.loads((machine.store.root / controller.WORKER_ENVIRONMENT).read_text())["environment"]
    assert "PYTHONHOME" not in saved
