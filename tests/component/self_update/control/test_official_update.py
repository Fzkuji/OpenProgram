"""An official release through the update controller: one record per candidate."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase
from openprogram.operations.self_update.control import official_update, switch
from openprogram.operations.self_update.official import replay
from openprogram.operations.self_update.official.assets import ReleaseError
from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.operations.self_update.versions.layout import Layout

from .test_switch import BASE, Host, NEW, OLD, _seal

TIP, RELEASE = "1" * 40, "2" * 40


class ReleaseHost(Host):
    def __init__(self, layout: Layout) -> None:
        super().__init__(layout)
        self.tip = TIP

    def go_back_local(self, update_id, commit, expected, version):
        self._do("go_back_local")
        self.moved = (update_id, commit, expected, version)


@pytest.fixture
def attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.state import paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    layout = Layout(state / "runtime")
    layout.root.mkdir(parents=True)
    layout.repo.mkdir()
    _seal(layout, OLD, commit=TIP)
    _seal(layout, NEW, commit=RELEASE)
    pointers.create(layout, pointers.Pointer(generation=1, version=OLD, base=BASE, last_good=OLD, last_good_base=BASE, writer="test"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "new.py\0", ""))
    monkeypatch.setattr(switch, "PROBATION_SECONDS", 0)
    store = SelfUpdateStore()
    host = ReleaseHost(layout)
    return official_update._Attempts(store, layout, host, OLD)


def test_a_passing_trial_keeps_its_record_for_activation(attempts) -> None:
    verdict = attempts.trial(NEW)
    record = attempts.store.load(attempts.update_id)
    assert verdict.passed and record.state.phase is UpdatePhase.READY
    assert record.request.kind == "official" and record.request.candidate_version == NEW
    assert record.request.base_sha == TIP and record.request.candidate_sha == RELEASE
    assert attempts.activate(NEW).passed
    assert attempts.store.load(attempts.update_id).state.phase is UpdatePhase.SUCCEEDED
    assert attempts.host.moved == (attempts.update_id, RELEASE, TIP, NEW)
    assert pointers.read(attempts.layout).version == NEW


def test_a_failed_trial_ends_its_record_and_frees_the_slot_for_the_next(attempts) -> None:
    attempts.host.fail["trial"] = switch.Failed("doctor regressed")
    verdict = attempts.trial(NEW)
    assert not verdict.passed and "doctor regressed" in verdict.reason
    first = attempts.update_id
    assert attempts.store.load(first).state.phase is UpdatePhase.ABORTED
    attempts.host.fail.clear()
    assert attempts.trial(NEW).passed
    assert attempts.update_id != first


def test_a_busy_slot_is_not_blamed_on_the_candidate(attempts) -> None:
    assert attempts.trial(NEW).passed
    held = attempts.update_id
    other = official_update._Attempts(attempts.store, attempts.layout, attempts.host, OLD)
    with pytest.raises(ReleaseError, match="another update"):
        other.trial(NEW)
    attempts.activate(NEW)
    assert attempts.store.load(held).state.phase is UpdatePhase.SUCCEEDED


def test_apply_release_refuses_while_an_update_is_running(attempts, monkeypatch) -> None:
    attempts.trial(NEW)
    called = []
    monkeypatch.setattr(replay, "replay_release", lambda *a, **k: called.append(1))
    result = official_update.apply_release(Path("/nowhere"), store=attempts.store, host=attempts.host,
                                           layout=attempts.layout, controller_version=OLD)
    assert result.outcome == replay.ABORTED and attempts.update_id in result.message and not called
    attempts.activate(NEW)
