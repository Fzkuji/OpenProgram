"""The worker's channel supervisor follows the account files while it runs."""
from __future__ import annotations

import threading

import pytest

import openprogram.interfaces.channels as channels_pkg
from openprogram.interfaces.channels import _heartbeats
from openprogram.interfaces.channels import accounts as accounts_mod
from openprogram.operations.worker import runner


class _FakeChannel:
    def __init__(self, label: str, runs: list) -> None:
        self.label = label
        self.runs = runs
        self.stopped = threading.Event()

    def run_forever(self, stop: threading.Event) -> None:
        self.runs.append(self)
        stop.wait(10)
        self.stopped.set()


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch):
    rows: list[dict] = []
    runs: list[_FakeChannel] = []
    creds: dict[str, object] = {}

    def row(account_id: str, **kw) -> dict:
        base = {"platform": "wechat", "account_id": account_id, "name": account_id,
                "enabled": True, "configured": True, "implemented": True}
        base.update(kw)
        return base

    def creds_path(channel: str, account_id: str):
        path = tmp_path / f"{channel}-{account_id}.json"
        if not path.exists():
            path.write_text("{}")
        creds[account_id] = path
        return path

    monkeypatch.setattr(channels_pkg, "list_status", lambda: list(rows))
    monkeypatch.setattr(channels_pkg, "build_channel",
                        lambda channel, account_id: _FakeChannel(f"{channel}:{account_id}", runs))
    monkeypatch.setattr(accounts_mod, "account_credentials_path", creds_path)
    sup = runner._ChannelSupervisor()
    yield sup, rows, row, runs, creds
    sup.shutdown()


def _wait(predicate, timeout: float = 2.0) -> bool:
    end = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        end.wait(0.02)
    return predicate()


def test_an_account_linked_after_start_starts_on_the_next_sync(world) -> None:
    sup, rows, row, runs, _creds = world
    rows.append(row("default"))
    assert sup.sync() == ["wechat:default"]
    rows.append(row("baba"))
    assert sup.sync() == ["wechat:baba"]
    assert sup.sync() == []
    assert _wait(lambda: len(runs) == 2)
    assert sorted(label for label, _ in sup.threads()) == ["wechat:baba", "wechat:default"]


def test_unusable_accounts_do_not_start(world, capsys) -> None:
    sup, rows, row, _runs, _creds = world
    rows.extend([row("off", enabled=False), row("nocreds", configured=False)])
    assert sup.sync() == []
    assert sup.sync() == []
    out = capsys.readouterr().out
    assert out.count("[wechat:off] disabled") == 1
    assert out.count("[wechat:nocreds] credentials missing") == 1


def test_a_disabled_account_stops(world) -> None:
    sup, rows, row, runs, _creds = world
    rows.append(row("baba"))
    sup.sync()
    assert _wait(lambda: len(runs) == 1)
    rows[0] = row("baba", enabled=False)
    sup.sync()
    assert runs[0].stopped.wait(2)
    assert sup.threads() == []


def test_a_stopped_adapter_restarts_only_after_a_new_login(world) -> None:
    sup, rows, row, runs, creds = world
    rows.append(row("baba"))
    sup.sync()
    assert _wait(lambda: len(runs) == 1)
    # The adapter gives up (expired login): same credentials, no restart loop.
    sup._running["wechat:baba"][1].set()
    assert runs[0].stopped.wait(2)
    assert _wait(lambda: not sup.threads()[0][1].is_alive())
    assert sup.sync() == []
    # A new QR login rewrites the credentials: the adapter starts again.
    import os
    path = creds["baba"]
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    assert sup.sync() == ["wechat:baba"]


class _SignedOutChannel:
    def run_forever(self, stop: threading.Event) -> None:
        return


class _CrashingChannel:
    def run_forever(self, stop: threading.Event) -> None:
        raise RuntimeError("boom")


class _StoppedByOwnerChannel:
    def run_forever(self, stop: threading.Event) -> None:
        stop.set()


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (_SignedOutChannel(), _heartbeats.STOP_SIGNED_OUT),
        (_CrashingChannel(), _heartbeats.STOP_FAILED),
        (_StoppedByOwnerChannel(), None),
    ],
)
def test_the_reason_an_adapter_stopped_is_kept(channel, expected) -> None:
    _heartbeats.clear_stop("wechat", "stop-test")
    runner._safe_run_channel("wechat:stop-test", channel, threading.Event())
    assert _heartbeats.get_stop("wechat", "stop-test") == expected
    _heartbeats.clear_stop("wechat", "stop-test")


def test_a_new_start_forgets_the_old_stop_and_a_failed_build_is_recorded(
    world, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sup, rows, row, _runs, _creds = world
    rows.append(row("stop-sync"))
    _heartbeats.record_stop("wechat", "stop-sync", _heartbeats.STOP_SIGNED_OUT)
    assert sup.sync() == ["wechat:stop-sync"]
    assert _heartbeats.get_stop("wechat", "stop-sync") is None

    def broken(channel: str, account_id: str):
        raise RuntimeError("no package")

    monkeypatch.setattr(channels_pkg, "build_channel", broken)
    rows.append(row("stop-build"))
    assert sup.sync() == []
    assert _heartbeats.get_stop("wechat", "stop-build") == _heartbeats.STOP_FAILED
    _heartbeats.clear_stop("wechat", "stop-build")
