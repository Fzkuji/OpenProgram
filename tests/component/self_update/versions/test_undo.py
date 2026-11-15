"""Undoing a change, and adopting version moves the controller did not make."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import pytest

from openprogram.operations.self_update.control import undo
from openprogram.operations.self_update.versions import pointer
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version, point_at

ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"],
    "HOME": "/var/empty",
}
_spec = importlib.util.spec_from_file_location("bootstrap_versions_undo", REPOSITORY / "scripts/release/bootstrap/versions.py")
versions_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(versions_tool)


def git(cwd, *arguments, check=True):
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *arguments],
        capture_output=True, text=True, env=ENVIRONMENT,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def line(tmp_path):
    """A repository whose ``local`` has four commits, the newest last."""
    build = tmp_path / "build"
    build.mkdir()
    git(build, "init", "-q", "--initial-branch=local")
    commits = []
    for number in range(1, 5):
        (build / f"file{number}.txt").write_text(f"change {number}\n")
        (build / "shared.txt").write_text("".join(f"line {n}\n" for n in range(1, number + 1)))
        git(build, "add", ".")
        git(build, "commit", "-qm", f"change {number}")
        commits.append(git(build, "rev-parse", "HEAD"))
    return build, commits


@pytest.fixture
def layout(tmp_path, line):
    build, _ = line
    result = make_layout(tmp_path / "state" / "runtime", tools=True)
    git(tmp_path, "clone", "-q", "--bare", str(build), str(result.repo))
    return result


def local(layout):
    return git(layout.repo, "rev-parse", "refs/heads/local")


def versions_of(layout, commits, *numbers):
    names = {}
    for number in numbers:
        name = f"v{number}"
        make_version(layout, name, commit=commits[number - 1])
        names[number] = name
    return names


def test_a_retained_version_is_gone_back_to(layout, line):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE)
    decided = undo.plan(layout, "v2")
    assert (decided.kind, decided.version, decided.base, decided.commit) == (undo.UNDO_TO, "v2", BASE, commits[1])
    fields = decided.request_fields()
    assert fields["kind"] == "undo_to" and fields["candidate_version"] == "v2"
    assert fields["candidate_sha"] == commits[1] and fields["base_sha"] == commits[3]
    assert commits[1] in decided.assertions[0]


def test_the_running_version_is_not_a_target(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    with pytest.raises(undo.UndoError, match="already the running version"):
        undo.plan(layout, "v4")


def test_a_version_no_longer_on_disk_is_built_from_its_commit(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    name = f"20261101T000000Z-{commits[1][:12]}"
    decided = undo.plan(layout, name)
    assert (decided.kind, decided.version, decided.commit) == (undo.UNDO_TO, None, commits[1])
    assert decided.request_fields()["candidate_version"] == ""


def test_a_version_whose_base_is_gone_is_built_again(layout, line):
    _, commits = line
    version_id = f"20261101T000000Z-{commits[1][:12]}"
    make_version(layout, version_id, commit=commits[1], base="py3.12-aaaaaaaaaaaaaaaa")
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    assert undo.plan(layout, version_id).version is None


def test_a_gone_version_on_the_undone_line_is_still_found(layout, line):
    _, commits = line
    versions_of(layout, commits, 2)
    point_at(layout, "v2")
    undo.commit_going_back(layout, "su_back", commits[1], expected_tip=commits[3])
    decided = undo.plan(layout, f"20261101T000000Z-{commits[3][:12]}")
    assert decided.commit == commits[3]


def test_an_unknown_version_says_so(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    with pytest.raises(undo.UndoError, match="no longer on disk"):
        undo.plan(layout, "20261101T000000Z-ffffffffffff")


def test_a_commit_with_later_ones_is_reverted_alone(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    decided = undo.plan(layout, commits[1][:10])
    assert decided.kind == undo.UNDO_COMMIT and decided.task == f"undo-{commits[1][:12]}"
    assert decided.request_fields()["task"] == decided.task
    assert "candidate_sha" not in decided.request_fields()
    # Reverting 2 needs 3 and 4 kept; file2 changed nowhere else, shared.txt conflicts.
    with pytest.raises(undo.RevertConflict) as caught:
        undo.create_revert(layout, decided)
    assert caught.value.files == ["shared.txt"]
    assert "shared.txt" in str(caught.value)
    assert not (layout.work / decided.task).exists()
    assert git(layout.repo, "branch", "--list", f"op/wt/{decided.task}") == ""
    assert local(layout) == commits[3]


def test_a_revert_lands_in_its_own_worktree_and_leaves_local_alone(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    git(build, "init", "-q", "--initial-branch=local")
    commits = []
    for number in range(1, 4):
        (build / f"file{number}.txt").write_text(f"change {number}\n")
        git(build, "add", ".")
        git(build, "commit", "-qm", f"change {number}")
        commits.append(git(build, "rev-parse", "HEAD"))
    layout = make_layout(tmp_path / "state" / "runtime")
    git(tmp_path, "clone", "-q", "--bare", str(build), str(layout.repo))
    make_version(layout, "v3", commit=commits[2])
    point_at(layout, "v3")
    decided = undo.plan(layout, commits[1])
    work = undo.create_revert(layout, decided)
    assert work.path == layout.work / decided.task
    assert work.branch == f"op/wt/{decided.task}"
    assert not (work.path / "file2.txt").exists() and (work.path / "file3.txt").exists()
    assert git(work.path, "rev-parse", "HEAD") == work.head != commits[2]
    assert git(work.path, "status", "--porcelain") == ""
    assert local(layout) == commits[2]
    assert git(work.path, "merge-base", "--is-ancestor", commits[2], work.head, check=False) == ""
    # A second attempt does not collide with the first one's worktree.
    assert undo.plan(layout, commits[1]).task == f"undo-{commits[1][:12]}-2"


def test_the_newest_commit_goes_back_to_the_version_before_it(layout, line):
    _, commits = line
    versions_of(layout, commits, 3, 4)
    point_at(layout, "v4")
    decided = undo.plan(layout, commits[3])
    assert (decided.kind, decided.version) == (undo.UNDO_TO, "v3")


def test_the_newest_commit_is_reverted_when_no_version_is_before_it(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    assert undo.plan(layout, commits[3]).kind == undo.UNDO_COMMIT


@pytest.mark.parametrize("target, message", [
    ("", "target is required"),
    ("nonsense!", "not a commit"),
    ("abcdef1234", "not part of the code"),
])
def test_bad_targets_are_refused(layout, line, target, message):
    _, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    with pytest.raises(undo.UndoError, match=message):
        undo.plan(layout, target)


def test_the_first_commit_and_merges_cannot_be_reverted(layout, line):
    build, commits = line
    versions_of(layout, commits, 4)
    point_at(layout, "v4")
    with pytest.raises(undo.UndoError, match="first commit"):
        undo.plan(layout, commits[0])
    git(build, "checkout", "-q", "-b", "side", commits[0])
    (build / "side.txt").write_text("side\n")
    git(build, "add", ".")
    git(build, "commit", "-qm", "side")
    git(build, "checkout", "-q", "local")
    git(build, "merge", "-q", "--no-edit", "side")
    merge = git(build, "rev-parse", "HEAD")
    git(layout.repo, "fetch", "-q", str(build), "+refs/heads/local:refs/heads/local")
    with pytest.raises(undo.UndoError, match="merges two lines"):
        undo.plan(layout, merge)


def test_going_back_keeps_the_old_tip_and_can_run_again(layout, line):
    _, commits = line
    reference = undo.commit_going_back(layout, "su_back", commits[1], expected_tip=commits[3])
    assert reference == "refs/openprogram/undone/su_back"
    assert local(layout) == commits[1]
    assert git(layout.repo, "rev-parse", reference) == commits[3]
    assert undo.commit_going_back(layout, "su_back", commits[1], expected_tip=commits[3]) == reference
    assert local(layout) == commits[1]


def test_going_back_refuses_a_line_that_moved(layout, line):
    _, commits = line
    with pytest.raises(undo.UndoError, match="moved"):
        undo.commit_going_back(layout, "su_back", commits[1], expected_tip=commits[2])
    assert local(layout) == commits[3]


def point_written_by(layout, version, writer, **fields):
    point_at(layout, version, **fields)
    return pointer.compare_and_set(layout, 5, writer=writer)


def test_a_move_by_versions_use_is_adopted_at_the_next_healthy_start(layout, line):
    _, commits = line
    versions_of(layout, commits, 1, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE, previous_good="v1", previous_good_base=BASE)
    assert versions_tool.use_version(layout.root, "v2") == 0
    moved = undo.reconcile_manual_move(layout, "v2")
    assert moved is not None and moved.moved_local and moved.returned_from is None
    assert local(layout) == commits[1]
    assert git(layout.repo, "rev-parse", moved.undone) == commits[3]
    assert git(layout.repo, "rev-parse", "refs/openprogram/version/v2") == commits[1]
    present = pointer.read(layout)
    assert (present.version, present.last_good, present.writer) == ("v2", "v2", undo.RECONCILE_WRITER)
    assert present.previous_good == "v1"
    # The next start finds nothing to adopt.
    assert undo.reconcile_manual_move(layout, "v2") is None


def test_a_move_on_to_the_same_line_only_marks_the_pointer(layout, line):
    _, commits = line
    versions_of(layout, commits, 4)
    point_written_by(layout, "v4", "shell")
    moved = undo.reconcile_manual_move(layout, "v4")
    assert moved is not None and moved.moved_local is False and moved.undone is None
    assert git(layout.repo, "for-each-ref", undo.UNDONE_PREFIX) == ""


@pytest.mark.parametrize("writer", ["controller", "installer", undo.RECONCILE_WRITER])
def test_other_writers_are_left_alone(layout, line, writer):
    _, commits = line
    versions_of(layout, commits, 2)
    point_written_by(layout, "v2", writer)
    assert undo.reconcile_manual_move(layout, "v2") is None
    assert local(layout) == commits[3]


def test_a_version_on_probation_is_left_to_its_controller(layout, line):
    _, commits = line
    versions_of(layout, commits, 2)
    point_written_by(layout, "v2", "launcher", probation="su_x", probation_deadline=2**31)
    assert undo.reconcile_manual_move(layout, "v2") is None


def test_a_worker_from_another_version_changes_nothing(layout, line):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_written_by(layout, "v2", "bootstrap")
    assert undo.reconcile_manual_move(layout, "v4") is None
    assert local(layout) == commits[3]


def test_a_return_by_the_guard_is_told_once_and_not_returned_to(layout, line):
    _, commits = line
    versions_of(layout, commits, 1, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE, previous_good="v2", previous_good_base=BASE)
    git(layout.repo, "update-ref", "refs/openprogram/version/v1", commits[0])
    # The launcher's guard: last_good would not start, so previous_good runs.
    pointer.compare_and_set(layout, 5, writer="launcher", version="v2", base=BASE)
    told = []
    moved = undo.reconcile_manual_move(layout, "v2", tell=lambda key, text: told.append((key, text)) or True)
    assert moved.returned_from == "v4"
    assert told == [("guard-6", "Version v4 did not start, so OpenProgram went back to version v2.")]
    present = pointer.read(layout)
    assert (present.last_good, present.previous_good) == ("v2", "v1")
    assert present.fallback() == ("v1", BASE)
    assert local(layout) == commits[1]


def test_with_nothing_else_kept_there_is_no_fallback_to_itself(layout, line):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE, previous_good="v2", previous_good_base=BASE)
    pointer.compare_and_set(layout, 5, writer="launcher", version="v2", base=BASE)
    undo.reconcile_manual_move(layout, "v2")
    present = pointer.read(layout)
    assert (present.previous_good, present.previous_good_base) == ("", "")
    assert present.fallback() is None


def test_a_return_during_probation_is_left_to_the_updates_result(layout, line):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", probation="su_x", probation_deadline=2**31, last_good="v2", last_good_base=BASE)
    pointer.compare_and_set(
        layout, 5, writer="launcher", version="v2", base=BASE, probation="", probation_deadline=None,
    )
    told = []
    moved = undo.reconcile_manual_move(layout, "v2", tell=lambda *pair: told.append(pair))
    assert told == [] and moved.returned_from is None


def test_a_failing_notice_does_not_stop_the_line_moving(layout, line):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE)
    pointer.compare_and_set(layout, 5, writer="launcher", version="v2", base=BASE)

    def boom(*_pair):
        raise RuntimeError("no database")

    moved = undo.reconcile_manual_move(layout, "v2", tell=boom)
    assert moved is not None and local(layout) == commits[1]


def test_a_crash_after_the_line_moved_finishes_on_the_next_start(layout, line, monkeypatch):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE)
    pointer.compare_and_set(layout, 5, writer="bootstrap", version="v2", base=BASE)

    def crash(*_args, **_kwargs):
        raise OSError("power went out")

    original = pointer.compare_and_set
    monkeypatch.setattr(undo.pointers, "compare_and_set", crash)
    with pytest.raises(OSError):
        undo.reconcile_manual_move(layout, "v2")
    assert local(layout) == commits[1]
    monkeypatch.setattr(undo.pointers, "compare_and_set", original)
    moved = undo.reconcile_manual_move(layout, "v2")
    assert moved is not None and moved.moved_local is False
    assert git(layout.repo, "rev-parse", "refs/openprogram/undone/manual-6") == commits[3]
    assert pointer.read(layout).last_good == "v2"


def test_a_pointer_that_moved_meanwhile_waits_for_the_next_start(layout, line, monkeypatch):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4", last_good="v4", last_good_base=BASE)
    pointer.compare_and_set(layout, 5, writer="bootstrap", version="v2", base=BASE)

    def conflict(*_args, **_kwargs):
        raise pointer.PointerConflict("moved")

    monkeypatch.setattr(undo.pointers, "compare_and_set", conflict)
    assert undo.reconcile_manual_move(layout, "v2") is None


def _write_worker_pid(state: Path, pid: int) -> None:
    (state / "worker.pid").write_text(f"{pid}\n")


def test_use_gives_a_worker_time_to_checkpoint(layout, line, tmp_path):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4")
    state = layout.root.parent
    marker = tmp_path / "checkpointed"
    worker = subprocess.Popen([
        sys.executable, "-c",
        "import pathlib,signal,sys,time\n"
        f"marker=pathlib.Path({str(marker)!r})\n"
        "def stop(*_):\n"
        "    time.sleep(0.2); marker.write_text('yes'); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n",
    ], stdout=subprocess.PIPE, text=True)
    assert worker.stdout.readline().strip() == "ready"
    threading.Thread(target=worker.wait, daemon=True).start()
    _write_worker_pid(state, worker.pid)
    assert versions_tool.use_version(layout.root, "v2") == 0
    assert marker.read_text() == "yes"
    assert pointer.read(layout).writer == "bootstrap"
    assert "moved from v4 to v2" in layout.recovery_log.read_text()


def test_use_does_not_wait_forever_for_a_broken_worker(layout, line, monkeypatch):
    _, commits = line
    versions_of(layout, commits, 2, 4)
    point_at(layout, "v4")
    state = layout.root.parent
    monkeypatch.setattr(versions_tool, "STOP_SECONDS", 0.3)
    worker = subprocess.Popen([
        sys.executable, "-c",
        "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(60)\n",
    ], stdout=subprocess.PIPE, text=True)
    assert worker.stdout.readline().strip() == "ready"
    threading.Thread(target=worker.wait, daemon=True).start()
    _write_worker_pid(state, worker.pid)
    assert versions_tool.use_version(layout.root, "v2") == 0
    assert worker.wait(timeout=10) == -signal.SIGKILL
    assert pointer.read(layout).version == "v2"
