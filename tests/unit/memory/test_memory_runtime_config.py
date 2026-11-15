"""Runtime-level memory configuration contracts."""

from __future__ import annotations

import sys

import pytest


def test_ensure_reports_missing_git(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)

    from openprogram.state.memory import store
    from openprogram.state.memory.management.transaction import TransactionError

    monkeypatch.setattr(store.shutil, "which", lambda _name: None)
    with pytest.raises(TransactionError) as caught:
        store.ensure()
    assert caught.value.code == "GIT_UNAVAILABLE"


def test_ensure_rechecks_git_after_repository_initialization(
    tmp_path, monkeypatch,
):
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)

    from openprogram.state.memory import store
    from openprogram.state.memory.management.transaction import TransactionError

    store.ensure()
    monkeypatch.setattr(store.shutil, "which", lambda _name: None)
    with pytest.raises(TransactionError) as caught:
        store.ensure()
    assert caught.value.code == "GIT_UNAVAILABLE"


def test_writer_model_setting_is_live_and_defaults_to_chat_agent():
    from openprogram.state.settings import get_settings

    row = next(
        item for item in get_settings() if item["key"] == "memory.writer.model"
    )
    assert row["value"] == ""
    assert row["apply"] == "live"


def test_commitment_heartbeat_settings_are_removed():
    from openprogram.state.settings import get_settings

    keys = {item["key"] for item in get_settings()}
    assert "proactive.heartbeat" not in keys
    assert "proactive.quiet_hours" not in keys


def test_backend_none_disables_every_runtime_surface(monkeypatch, tmp_path):
    import openprogram.state.memory as memory

    monkeypatch.setattr(
        "openprogram.state.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"memory": {"backend": "none"}},
    )
    monkeypatch.setattr(memory, "_backend", None)

    backend = memory.get_backend()
    assert backend.name == "none"
    assert backend.system_prompt() == ""
    assert backend.search("remember this") == ""
    assert backend.write(session_id="s1", force=True) is None
    assert backend.reorganize() == {"status": "disabled"}

    from openprogram.state.memory import writing

    assert writing.write("s1", token_threshold=1, force=True) is None
    assert writing.reorganize() == {"status": "disabled"}

    from openprogram.state.memory.scheduler import start_nightly_reorganizer
    from openprogram.state.memory.session_watcher import start_idle_session_watcher

    assert start_nightly_reorganizer(initial_delay=0) is None
    assert start_idle_session_watcher(poll_interval=1) is None
    assert not (tmp_path / "state" / "memory").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["status"],
        ["recall", "anything"],
        ["show", "topics/example.md"],
        ["edit", "topics/example.md"],
        ["sleep"],
        ["export"],
    ],
)
def test_backend_none_rejects_every_cli_memory_verb(
    monkeypatch, tmp_path, capsys, arguments
):
    monkeypatch.setattr(
        "openprogram.state.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"memory": {"backend": "none"}},
    )
    monkeypatch.setattr(sys, "argv", ["openprogram", "memory", *arguments])

    from openprogram.interfaces.cli.application import main

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 1
    assert "memory is disabled by memory.backend=none" in capsys.readouterr().out
    assert not (tmp_path / "state" / "memory").exists()
