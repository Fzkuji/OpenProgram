"""An official update prepared in the background and switched to on restart."""
from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase
from openprogram.operations.self_update.control import official_update, switch, watchdog
from openprogram.operations.self_update.official import fetch, replay
from openprogram.operations.self_update.official.channel import Channel
from openprogram.operations.self_update.official.repository import ReleaseError
from openprogram.operations.self_update.types import is_held
from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.host._platform.runtime_layout import Layout

from ..control.test_official_update import RELEASE, TIP, ReleaseHost
from ..control.test_switch import BASE, NEW, OLD, _seal

TARGET = "0.2.0"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.state import paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    layout = Layout(state / "runtime")
    layout.root.mkdir(parents=True)
    layout.repo.mkdir()
    _seal(layout, OLD, commit=TIP)
    _seal(layout, NEW, commit=RELEASE)
    pointers.create(layout, pointers.Pointer(generation=1, version=OLD, base=BASE, last_good=OLD,
                                             last_good_base=BASE, writer="test"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "new.py\0", ""))
    monkeypatch.setattr(switch, "PROBATION_SECONDS", 0)
    monkeypatch.setattr(official_update, "_tell_owner", lambda result: None)
    monkeypatch.setattr("openprogram.operations.self_update.repair.diagnosis.dispatch_pending", lambda: None)
    store = SelfUpdateStore()
    return store, layout, ReleaseHost(layout)


def _found(target: str = TARGET) -> fetch.Discovery:
    published = Channel(sequence=1, published_at="2026-01-01T00:00:00Z", version=target, announce=True,
                        build="a" * 12, notes="", commit="a" * 40, shell_compat=1, layers={}, packs={})
    return fetch.Discovery(None, published)


def _prepare(world, target: str = TARGET) -> str:
    """Prepare ``NEW`` for ``target`` the way ``upgrade --prepare`` does, without the network."""
    store, layout, host = world
    hold = _found(target).hold()
    attempts = official_update._Attempts(store, layout, host, OLD, hold)
    assert attempts.trial(NEW).passed and attempts.hold(NEW).passed
    return str(attempts.update_id)


def test_a_prepared_update_is_held_in_ready_and_nothing_switches(world) -> None:
    store, layout, host = world
    update_id = _prepare(world)
    record = store.load(update_id)
    assert record.state.phase is UpdatePhase.READY and is_held(record)
    assert record.state.detail["target"] == TARGET
    assert pointers.read(layout).version == OLD
    assert "stop_worker" not in host.calls
    assert switch.controller_process(store, record) is None  # The lock was let go.
    assert official_update.describe_held(record) == {
        "version": NEW, "target": TARGET, "channel": "stable", "latest_version": TARGET,
                                  "announce": True}


def test_a_controller_watchdog_or_worker_start_leaves_a_held_update_alone(world, monkeypatch) -> None:
    store, layout, host = world
    update_id = _prepare(world)
    launched: list[str] = []
    monkeypatch.setattr(switch, "launch", lambda update_id, store=None: launched.append(update_id))
    watchdog._launched.clear()

    assert watchdog.tick(store) is None
    switch.reconcile_at_start(store, store.load(update_id))
    switch.SwitchController(store, update_id, host, layout).advance()
    assert switch.run(update_id, store=store, host=host) == 0

    assert launched == [] and store.load(update_id).state.phase is UpdatePhase.READY
    assert pointers.read(layout).version == OLD


def test_another_update_cancels_a_held_one(world) -> None:
    store, _, _ = world
    update_id = _prepare(world)
    request = replace(store.load(update_id).request, update_id="su_tool")
    store.create(request)
    cancelled = store.load(update_id)
    assert cancelled.state.phase is UpdatePhase.ABORTED and cancelled.state.detail["cancelled"]
    assert store.load_active().request.update_id == "su_tool"


def test_preparing_the_same_target_again_does_nothing(world, monkeypatch) -> None:
    store, _, _ = world
    update_id = _prepare(world)
    monkeypatch.setattr(fetch, "discover", lambda **_: _found())
    monkeypatch.setattr(fetch, "update", lambda **_: pytest.fail("nothing is downloaded again"))
    result, about = official_update.prepare_update(store=store)
    assert result.outcome == official_update.PREPARED and result.version == NEW
    assert about == {"channel": "stable", "latest_version": TARGET,
                                  "announce": True}
    assert store.load(update_id).state.phase is UpdatePhase.READY


def test_a_newer_target_replaces_the_held_update(world, monkeypatch) -> None:
    store, _, _ = world
    update_id = _prepare(world)
    seen = []
    monkeypatch.setattr(fetch, "discover", lambda **_: _found("0.3.0"))
    monkeypatch.setattr(fetch, "update", lambda **options: seen.append(options) or replay.ReplayResult(
        outcome=official_update.PREPARED, message="", release="0.3.0"))
    result, about = official_update.prepare_update(store=store)
    assert store.load(update_id).state.phase is UpdatePhase.ABORTED
    assert seen[0]["hold"] is True and seen[0]["found"].target == "0.3.0"
    assert about["latest_version"] == "0.3.0"


def test_upgrade_switches_to_the_held_update(world, monkeypatch) -> None:
    store, layout, host = world
    update_id = _prepare(world)
    monkeypatch.setattr(fetch, "discover", lambda **_: _found())
    monkeypatch.setattr(fetch, "update", lambda **_: pytest.fail("the held update is used"))
    result, _ = official_update.apply_update(store=store, host=host, layout=layout)
    assert result.outcome == replay.ACTIVATED
    assert store.load(update_id).state.phase is UpdatePhase.SUCCEEDED
    assert pointers.read(layout).version == NEW


def test_offline_the_held_update_is_still_switched_to(world, monkeypatch) -> None:
    store, layout, host = world
    _prepare(world)

    def offline(**_):
        raise ReleaseError("offline")

    monkeypatch.setattr(fetch, "discover", offline)
    result, about = official_update.apply_update(store=store, host=host, layout=layout)
    assert result.outcome == replay.ACTIVATED and about["latest_version"] == TARGET


def test_a_held_update_left_behind_by_local_is_installed_the_usual_way(world, monkeypatch) -> None:
    store, layout, host = world
    update_id = _prepare(world)
    host.tip = "9" * 40
    seen = []
    monkeypatch.setattr(fetch, "discover", lambda **_: _found())
    monkeypatch.setattr(fetch, "update", lambda **options: seen.append(options) or replay.ReplayResult(
        outcome=replay.ACTIVATED, message="", release=TARGET))
    official_update.apply_update(store=store, host=host, layout=layout)
    assert store.load(update_id).state.phase is UpdatePhase.ABORTED and len(seen) == 1
    assert "hold" not in seen[0]


def test_a_held_update_whose_staged_app_is_gone_is_installed_again_not_held_forever(world, monkeypatch) -> None:
    store, layout, host = world
    update_id = _prepare(world)
    # It needs the new App shell, and the App staged for it is gone (no record, no running App to swap).
    monkeypatch.setattr(official_update, "shell_hosts", lambda *args, **options: False)
    seen = []
    monkeypatch.setattr(fetch, "discover", lambda **_: _found())
    monkeypatch.setattr(fetch, "update", lambda **options: seen.append(options) or replay.ReplayResult(
        outcome=official_update.PREPARED, message="The new App is ready; restart to update.", release=TARGET))
    result, _ = official_update.apply_update(store=store, host=host, layout=layout)
    assert store.load(update_id).state.phase is UpdatePhase.ABORTED, "not reported as prepared again"
    assert len(seen) == 1 and result.outcome == official_update.PREPARED, "the App is staged again"
    assert official_update.held_update(store) is None and pointers.read(layout).version == OLD


# --- The command --------------------------------------------------------------


def _arguments(**values):
    return Namespace(**{"channel": None, "json": True, "check": False, "dry_run": False, "release_directory": None,
                        "upgrade_verb": None, "prepare": False, **values})


@pytest.fixture
def version_store(monkeypatch):
    from types import SimpleNamespace

    from openprogram.host._platform import runtime_layout as layouts

    monkeypatch.setattr(layouts, "current_runtime", lambda: SimpleNamespace(layout=None, version="v"))


def test_upgrade_prepare_json(version_store, monkeypatch, capsys) -> None:
    from openprogram.interfaces.cli.commands.updates import upgrade

    outcome = replay.ReplayResult(outcome=official_update.PREPARED, message="0.2.0 is ready.", release=TARGET)
    monkeypatch.setattr(official_update, "prepare_update",
                        lambda **_: (outcome, {"channel": "stable", "latest_version": TARGET,
                                  "announce": True}))
    assert upgrade._cmd_upgrade(_arguments(prepare=True)) == 0
    report = json.loads(capsys.readouterr().out)
    assert {key: report[key] for key in ("ok", "outcome", "prepared", "applied", "latest_version", "channel")} == {
        "ok": True, "outcome": "prepared", "prepared": True, "applied": False,
        "latest_version": TARGET, "channel": "stable"}
    assert report["detail"] == "0.2.0 is ready."


def test_upgrade_check_json_names_the_held_update(world, version_store, monkeypatch, capsys) -> None:
    from openprogram.interfaces.cli.commands.updates import upgrade

    _prepare(world)
    monkeypatch.setattr(fetch, "check", lambda **_: {"current_version": "0.1.0", "latest_version": TARGET,
                                                 "update_available": True, "can_apply": True, "detail": "",
                                                 "download_bytes": 0})
    assert upgrade._cmd_upgrade(_arguments(check=True)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["prepared"] == {"version": NEW, "target": TARGET, "channel": "stable", "latest_version": TARGET,
                                  "announce": True}
