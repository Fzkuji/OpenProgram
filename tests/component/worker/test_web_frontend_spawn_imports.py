from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openprogram import _compat
from openprogram.worker import web


@pytest.fixture
def frontend(monkeypatch, tmp_path):
    """Run the public startup path with no real subprocesses or watcher thread."""
    (tmp_path / ".next").mkdir()
    build_id = tmp_path / ".next" / "BUILD_ID"
    build_id.write_text("initial", encoding="utf-8")
    monkeypatch.delenv("OPENPROGRAM_NO_WEB", raising=False)
    monkeypatch.setattr(web, "web_dir", lambda: tmp_path)
    monkeypatch.setattr(web, "_node_available", lambda: True)
    monkeypatch.setattr(web, "_ensure_built", Mock(return_value=True))
    monkeypatch.setattr(web, "_reclaim_web_port", Mock())
    monkeypatch.setattr(web, "_patch_manifest_ports", Mock(return_value=True))
    monkeypatch.setattr(web, "_live_proc", None)
    thread = Mock()
    monkeypatch.setattr(web, "_threading", SimpleNamespace(Thread=thread))
    popen = Mock()
    monkeypatch.setattr(web.subprocess, "Popen", popen)
    return SimpleNamespace(build_id=build_id, thread=thread, popen=popen)


def run_rebuilds(frontend, monkeypatch):
    builds = iter(["second", "third"])

    def tick(_seconds):
        frontend.build_id.write_text(next(builds), encoding="utf-8")

    monkeypatch.setattr(time, "sleep", tick)
    callback = frontend.thread.call_args.kwargs["target"]
    with pytest.raises(StopIteration):
        callback()


@pytest.mark.parametrize("flags", [0, 0x08000000])
@pytest.mark.parametrize("respawn_fails", [False, True])
def test_start_and_rebuilds_use_platform_flags(frontend, monkeypatch, flags, respawn_fails):
    helper = Mock(return_value=flags)
    monkeypatch.setattr(_compat, "no_window_creation_flags", helper)
    initial, second, third = (Mock(pid=pid) for pid in (101, 102, 103))
    frontend.popen.side_effect = [
        initial, OSError("spawn failed") if respawn_fails else second, third,
    ]

    assert web.start_web_frontend(backend_port=18099, web_port=18100) is initial
    frontend.thread.return_value.start.assert_called_once_with()
    run_rebuilds(frontend, monkeypatch)

    assert helper.call_count == 3
    assert frontend.popen.call_count == 3
    for call in frontend.popen.call_args_list:
        assert call.kwargs["creationflags"] == flags
        assert call.kwargs["env"]["OPENPROGRAM_BACKEND_URL"] == "http://127.0.0.1:18099"
        assert call.kwargs["env"]["PORT"] == "18100"
    assert initial.terminate.called
    if not respawn_fails:
        second.terminate.assert_called_once_with()
    assert web._live_proc is third
    web.stop_web_frontend(initial)
    third.terminate.assert_called_once_with()
    third.wait.assert_called_once_with(timeout=5.0)


def test_initial_spawn_error_returns_none(frontend, monkeypatch, capsys):
    monkeypatch.setattr(_compat, "no_window_creation_flags", lambda: 0)
    frontend.popen.side_effect = OSError("spawn failed")

    assert web.start_web_frontend(backend_port=18099, web_port=18100) is None
    frontend.thread.assert_not_called()
    assert "failed to spawn next start: spawn failed" in capsys.readouterr().out
