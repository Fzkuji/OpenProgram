"""The pointer's format and atomic write procedure."""
import os
import time

import pytest

from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.operations.self_update.versions.layout import Layout

BASE = "py3.12-9f2c41aa77d04c11"
DOCUMENTED = f"""schema=1
generation=17
version=20261108T153012Z-3b08fd06e123
base={BASE}
last_good=20261101T101500Z-aa11bb22cc33
last_good_base={BASE}
previous_good=20261020T091100Z-77ee11aa22bb
previous_good_base={BASE}
probation=u_7f3a9c
probation_deadline=1794134010
writer=controller
"""


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path)


def _seed(layout, **changes):
    fields = {"generation": 1, "version": "v1", "base": BASE, "writer": "test", **changes}
    return pointers.create(layout, pointers.Pointer(**fields))


def test_the_documented_format_round_trips():
    parsed = pointers.parse(DOCUMENTED)
    assert parsed.generation == 17 and parsed.probation_deadline == 1794134010
    assert pointers.render(parsed) == DOCUMENTED


def test_unknown_lines_are_kept():
    parsed = pointers.parse(DOCUMENTED + "future=1\n")
    assert pointers.render(parsed).endswith("future=1\n")


@pytest.mark.parametrize("text", [
    "", "schema=2\ngeneration=1\nversion=v\nbase=b\n", "schema=1\ngeneration=x\nversion=v\nbase=b\n",
    "schema=1\ngeneration=1\nversion=../v\nbase=b\n", "schema=1\ngeneration=1\nversion=v\n",
    "schema=1\nschema=1\ngeneration=1\nversion=v\nbase=b\n", "not a pointer\n",
    "schema=1\ngeneration=1\nversion=v\nbase=b\nnote=é\n",
])
def test_malformed_pointers_are_refused(text):
    with pytest.raises(pointers.PointerError):
        pointers.parse(text)


def test_compare_and_set_advances_and_keeps_the_previous(layout):
    _seed(layout)
    following = pointers.compare_and_set(layout, 1, writer="controller", version="v2")
    assert following.generation == 2
    assert pointers.read(layout).version == "v2"
    assert pointers.parse(layout.previous_pointer.read_text()).version == "v1"
    assert sorted(path.name for path in layout.root.iterdir()) == ["current", "current.prev"]


def test_a_stale_generation_changes_nothing(layout):
    _seed(layout)
    pointers.compare_and_set(layout, 1, writer="a", version="v2")
    with pytest.raises(pointers.PointerConflict):
        pointers.compare_and_set(layout, 1, writer="b", version="v3")
    assert pointers.read(layout).version == "v2"


def test_an_invalid_id_never_reaches_the_file(layout):
    _seed(layout)
    with pytest.raises(pointers.PointerError):
        pointers.compare_and_set(layout, 1, writer="a", version="../evil")
    assert pointers.read(layout).version == "v1"


def test_a_stale_lock_is_broken_and_a_fresh_one_is_not(layout, monkeypatch):
    _seed(layout)
    layout.lock.write_text("")
    old = time.time() - pointers.LOCK_BREAK_SECONDS - 1
    os.utime(layout.lock, (old, old))
    pointers.compare_and_set(layout, 1, writer="a", version="v2")
    assert not layout.lock.exists()
    layout.lock.write_text("")
    monkeypatch.setattr(pointers, "LOCK_WAIT_SECONDS", 0.05)
    original = pointers._acquire
    monkeypatch.setattr(pointers, "_acquire", lambda current: original(current, wait=0.05))
    with pytest.raises(pointers.PointerConflict):
        pointers.compare_and_set(layout, 2, writer="a", version="v3")


def test_commit_shifts_the_good_pairs(layout):
    _seed(layout, version="v3", last_good="v2", last_good_base=BASE, previous_good="v1",
          previous_good_base=BASE, probation="u1", probation_deadline=99)
    after = pointers.commit(layout, 1)
    assert (after.last_good, after.previous_good) == ("v3", "v2")
    assert after.probation == "" and after.probation_deadline is None


def test_a_good_pointer_falls_back_to_last_good_and_last_good_to_previous():
    candidate = pointers.Pointer(1, "v3", BASE, "v2", BASE, "v1", BASE)
    assert candidate.fallback() == ("v2", BASE)
    assert pointers.Pointer(1, "v2", BASE, "v2", BASE, "v1", BASE).fallback() == ("v1", BASE)
    assert pointers.Pointer(1, "v2", BASE, "v2", BASE).fallback() is None


def test_a_damaged_pointer_is_recovered_from_the_previous_one(layout):
    _seed(layout)
    pointers.compare_and_set(layout, 1, writer="a", version="v2")
    layout.pointer.write_text("garbage")
    assert pointers.read_or_recover(layout).version == "v1"


def test_rollback_moves_to_last_good(layout):
    _seed(layout, version="v2", last_good="v1", last_good_base=BASE, probation="u1", probation_deadline=9)
    assert pointers.rollback(layout, "v2", deadline=time.time() + 1) == "moved"
    after = pointers.read(layout)
    assert after.version == "v1" and after.probation == ""


def test_rollback_after_the_guard_already_moved_is_done(layout):
    _seed(layout, version="v1")
    assert pointers.rollback(layout, "v2", deadline=time.time() + 1) == "already"


def test_a_rollback_that_cannot_write_is_journaled(layout, monkeypatch):
    _seed(layout, version="v2", last_good="v1", last_good_base=BASE)
    def refuse(*_args, **_kwargs):
        raise pointers.PointerConflict("busy")
    monkeypatch.setattr(pointers, "compare_and_set", refuse)
    assert pointers.rollback(layout, "v2", deadline=0, sleep=lambda _: None) == "pending"
    assert layout.pending_rollback.read_text() == "version=v2\n"


def test_a_commit_leaves_no_probation_for_a_repair_to_bring_back(tmp_path):
    layout = Layout(tmp_path / "runtime")
    layout.root.mkdir()
    pointers.create(layout, pointers.Pointer(generation=1, version="old", base=BASE, last_good="old", last_good_base=BASE))
    pointers.activate(layout, 1, version="new", base=BASE, update_id="su_1", deadline=1)
    committed = pointers.commit(layout, 2)

    layout.pointer.write_text("not a pointer")
    repaired = pointers.read_or_recover(layout)

    assert repaired.version == "new" and repaired.probation == "" and repaired.probation_deadline is None
    assert repaired.last_good == committed.last_good == "new" and repaired.previous_good == "old"


# Windows: another handle on a pointer file refuses the open, rename or delete
# for a moment. These run the Windows branches against stand-ins; they have not
# run on Windows.


def test_a_lock_that_cannot_be_removed_does_not_undo_a_finished_move(layout, monkeypatch):
    _seed(layout)
    real = type(layout.lock).unlink
    refusals = []

    def unlink(self, missing_ok=False):
        if self.name == "current.lock":
            refusals.append(self)
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(layout.lock), "unlink", unlink)
    monkeypatch.setattr(pointers, "_WINDOWS", True)
    monkeypatch.setattr(pointers, "_WINDOWS_RETRY_SECONDS", 0)
    monkeypatch.setattr(pointers, "_replace", lambda source, target: os.replace(source, target))
    moved = pointers.compare_and_set(layout, 1, writer="a", version="v2")
    assert moved.version == "v2" and pointers.read(layout).version == "v2"
    assert len(refusals) == pointers._WINDOWS_RETRIES, "the delete is retried through the sharing violation"
    # The lock left behind expires by age, as a crashed writer's does.
    monkeypatch.undo()
    old = time.time() - pointers.LOCK_BREAK_SECONDS - 1
    os.utime(layout.lock, (old, old))
    assert pointers.compare_and_set(layout, 2, writer="a", version="v3").version == "v3"


def test_a_lock_released_after_a_brief_refusal_is_removed(layout, monkeypatch):
    _seed(layout)
    real = type(layout.lock).unlink
    refusals = []

    def unlink(self, missing_ok=False):
        if self.name == "current.lock" and len(refusals) < 2:
            refusals.append(self)
            raise PermissionError(13, "Access is denied")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(layout.lock), "unlink", unlink)
    monkeypatch.setattr(pointers, "_WINDOWS", True)
    monkeypatch.setattr(pointers, "_WINDOWS_RETRY_SECONDS", 0)
    monkeypatch.setattr(pointers, "_replace", lambda source, target: os.replace(source, target))
    pointers.compare_and_set(layout, 1, writer="a", version="v2")
    assert not layout.lock.exists()


def test_a_lock_that_cannot_be_read_ends_in_a_conflict_not_a_spin(layout, monkeypatch):
    _seed(layout)
    layout.lock.write_text("")
    real = type(layout.lock).stat
    calls = []

    def stat(self, **options):
        if self.name == "current.lock":
            calls.append(self)
            if len(calls) > 500:
                raise AssertionError("the writer spins on a lock it cannot read")
            raise PermissionError(13, "Access is denied")  # Windows: the file is being deleted.
        return real(self, **options)

    monkeypatch.setattr(type(layout.lock), "stat", stat)
    with pytest.raises(pointers.PointerConflict):
        pointers._acquire(layout, wait=0.2)
    assert len(calls) < 20


class _Kernel32:
    """``MoveFileExW`` that fails with a sharing violation twice, then renames."""

    def __init__(self, state):
        self.state = state

    @property
    def MoveFileExW(self):
        state = self.state

        class Call:
            argtypes = restype = None

            def __call__(self, source, target, flags):
                state["flags"] = flags
                state["calls"] += 1
                if state["calls"] <= 2:
                    state["last_error"] = 32
                    return 0
                os.replace(source, target)
                return 1

        return Call()


def test_a_windows_rename_reads_its_error_code_and_retries(layout, monkeypatch):
    import ctypes

    _seed(layout)
    state = {"calls": 0, "last_error": 0}

    def win_dll(name, use_last_error=False):
        assert name == "kernel32"
        state["saves_last_error"] = use_last_error
        return _Kernel32(state)

    def win_error(code=None):
        # Without a code ctypes asks the thread, whose value Python may already have overwritten.
        code = 0 if code is None else code
        return PermissionError(13, "sharing violation") if code == 32 else OSError(22, "The operation completed successfully")

    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    monkeypatch.setattr(ctypes, "windll", type("windll", (), {"kernel32": _Kernel32(state)})(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["last_error"] if state.get("saves_last_error") else 0,
                        raising=False)
    monkeypatch.setattr(ctypes, "WinError", win_error, raising=False)
    monkeypatch.setattr(pointers, "_WINDOWS", True)
    monkeypatch.setattr(pointers, "_WINDOWS_RETRY_SECONDS", 0)
    source = layout.root / "current.1.tmp"
    source.write_text("x")
    pointers._replace(source, layout.root / "moved")
    assert (layout.root / "moved").read_text() == "x" and not source.exists()
    assert state["calls"] == 3 and state["flags"] == 0x1 | 0x8, "replace existing, write through"
