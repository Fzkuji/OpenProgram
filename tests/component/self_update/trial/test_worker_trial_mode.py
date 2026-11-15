"""What a trial does not start, and what it leaves behind."""
from __future__ import annotations

import os
import time

from openprogram.operations.self_update.delivery import launcher
from openprogram.operations.self_update.trial import instance
from openprogram.operations.self_update.versions.layout import Layout
from openprogram.operations.worker.runner import trial_mode


def test_trial_mode_follows_the_environment(monkeypatch):
    monkeypatch.delenv("OPENPROGRAM_TRIAL", raising=False)
    assert trial_mode() is False
    monkeypatch.setenv("OPENPROGRAM_TRIAL", "1")
    assert trial_mode() is True


def test_a_trial_never_reaches_the_service_manager(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_TRIAL", "1")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not submit")
    monkeypatch.setattr(launcher, "_submit_supervisor", forbidden)
    assert launcher.launch_supervisor("su_anything", resume=True).label.endswith("su_anything")


def test_what_a_failed_trial_left_goes_after_seven_days(tmp_path):
    layout = Layout(tmp_path)
    old = layout.trials / "su_old"
    new = layout.trials / "su_new"
    old.mkdir(parents=True)
    new.mkdir()
    moment = time.time() - instance.KEPT_FAILED_SECONDS - 60
    os.utime(old, (moment, moment))
    assert instance.prune_trials(layout) == ["su_old"]
    assert new.is_dir() and not old.exists()


def test_a_finished_trial_keeps_only_logs_when_it_failed(tmp_path):
    workspace = instance.Workspace(tmp_path / "trial").create()
    workspace.state_dir.mkdir(parents=True)
    (workspace.directory / "candidate.log").write_text("log")
    workspace.finish(passed=False)
    assert {path.name for path in workspace.directory.iterdir()} == {"candidate.log"}
    workspace.finish(passed=True)
    assert not workspace.directory.exists()
