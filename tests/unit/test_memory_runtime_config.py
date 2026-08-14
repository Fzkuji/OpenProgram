"""Runtime-level memory configuration contracts."""

from __future__ import annotations

import sys

import pytest


def test_writer_model_setting_is_live_and_defaults_to_chat_agent():
    from openprogram.config_schema import get_settings

    row = next(
        item for item in get_settings() if item["key"] == "memory.writer.model"
    )
    assert row["value"] == ""
    assert row["apply"] == "live"


def test_commitment_heartbeat_settings_are_removed():
    from openprogram.config_schema import get_settings

    keys = {item["key"] for item in get_settings()}
    assert "proactive.heartbeat" not in keys
    assert "proactive.quiet_hours" not in keys


def test_backend_none_disables_every_runtime_surface(monkeypatch, tmp_path):
    import openprogram.memory as memory

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"memory": {"backend": "none"}},
    )
    monkeypatch.setattr(memory, "_backend", None)

    backend = memory.get_backend()
    assert backend.name == "none"
    assert backend.system_prompt() == ""
    assert backend.search("remember this") == ""
    assert backend.write(session_id="s1", force=True) is None
    assert backend.reorganize() == {"status": "disabled"}

    from openprogram.memory import writing

    assert writing.write("s1", token_threshold=1, force=True) is None
    assert writing.reorganize() == {"status": "disabled"}

    from openprogram.memory.scheduler import start_nightly_reorganizer
    from openprogram.memory.session_watcher import start_idle_session_watcher

    assert start_nightly_reorganizer(initial_delay=0) is None
    assert start_idle_session_watcher(poll_interval=1) is None

    from openprogram.memory.writing import (
        archive_unpaired_group_message,
    )

    assert archive_unpaired_group_message(
        channel="telegram", account_id="main", chat_id="group-1",
        message_id="m1", user_id="u1", user_display="U", text="pending",
    ) == ""
    assert not (tmp_path / "state" / "memory").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["status"],
        ["recall", "anything"],
        ["show", "topics/example.md"],
        ["edit", "topics/example.md"],
        ["sleep"],
        ["backfill"],
        ["export"],
    ],
)
def test_backend_none_rejects_every_cli_memory_verb(
    monkeypatch, tmp_path, capsys, arguments
):
    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"memory": {"backend": "none"}},
    )
    monkeypatch.setattr(sys, "argv", ["openprogram", "memory", *arguments])

    from openprogram.cli import main

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 1
    assert "memory is disabled by memory.backend=none" in capsys.readouterr().out
    assert not (tmp_path / "state" / "memory").exists()
