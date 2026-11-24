"""Open windows read again when told: each producer signals its topic.

The Programs, Applications, MCP, Channels and Meetings pages no longer
re-read on a timer; these are the places that tell them.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openprogram.host._platform import changes


def _marks(listener: changes.Listener) -> set[str]:
    return listener.wait(0)


# -- channels ---------------------------------------------------------------

def test_a_heartbeat_signals_when_it_appears_or_returns_not_on_each_refresh() -> None:
    from openprogram.interfaces.channels import _heartbeats as beats
    beats.clear("faketg", "topic")
    beats.clear_stop("faketg", "topic")
    with changes.listen("channels") as listener:
        beats.heartbeat("faketg", "topic", ts=100.0)
        assert _marks(listener) == {"channels:faketg:topic"}
        beats.heartbeat("faketg", "topic", ts=105.0)
        assert _marks(listener) == set()
        # Back after going stale reads differently.
        beats.heartbeat("faketg", "topic", ts=105.0 + beats.STALE_AFTER_SECONDS)
        assert _marks(listener) == {"channels:faketg:topic"}
        beats.record_retry("faketg", "topic", settles_in=60)
        assert _marks(listener) == {"channels:faketg:topic"}
        beats.record_stop("faketg", "topic", beats.STOP_FAILED)
        assert _marks(listener) == {"channels:faketg:topic"}
        beats.clear_stop("faketg", "topic")
        assert _marks(listener) == {"channels:faketg:topic"}
        beats.clear_stop("faketg", "topic")
        assert _marks(listener) == set()
        beats.clear("faketg", "topic")
        assert _marks(listener) == {"channels:faketg:topic"}


def test_an_account_change_signals_its_key(tmp_path, monkeypatch) -> None:
    from openprogram.interfaces.channels import accounts
    monkeypatch.setattr(accounts, "_channels_root", lambda: tmp_path)
    monkeypatch.setattr(accounts, "account_meta_path",
                        lambda c, a: tmp_path / c / "accounts" / a / "account.json")
    (tmp_path / "telegram" / "accounts" / "bot").mkdir(parents=True)
    with changes.listen("channels") as listener:
        accounts.set_enabled("telegram", "bot", False)
        assert _marks(listener) == {"channels:telegram:bot"}


def test_the_account_key_of_a_watched_file() -> None:
    from openprogram.interfaces.server.routes.settings.channels import _account_key
    root = Path("/state/channels")
    assert _account_key(root / "wechat" / "accounts" / "baba" / "account.json") == "wechat:baba"
    assert _account_key(root / "session_generations.json") is None


# -- programs and applications ----------------------------------------------

def test_a_rescan_and_a_disposed_package_signal_programs(monkeypatch) -> None:
    from openprogram.programs._host import registry
    from openprogram.programs._host.ui import loader
    monkeypatch.setattr(registry, "load_program_modules", lambda *_: None)
    monkeypatch.setattr(loader, "active", lambda: False)
    with changes.listen("programs") as listener:
        registry.rescan()
        assert _marks(listener) == {"programs"}
        registry.dispose_program("tool:not-loaded")
        assert _marks(listener) == set()


def test_loading_or_unloading_an_application_signals_it(monkeypatch) -> None:
    from openprogram.programs._host.ui import loader
    monkeypatch.setattr(loader, "_load", lambda app_id: None)
    monkeypatch.setattr("openprogram.programs._host.registry.dispose_program", lambda owner: [])
    with changes.listen("applications") as listener:
        loader.load("demo")
        assert _marks(listener) == {"applications:demo"}
        loader.unload("demo")
        assert _marks(listener) == {"applications:demo"}


def test_writing_the_application_catalog_signals_applications(tmp_path, monkeypatch) -> None:
    from openprogram.programs._host.ui import catalog_store
    monkeypatch.setattr(catalog_store, "path", lambda: tmp_path / "catalog.json")
    with changes.listen("applications") as listener:
        catalog_store.write([])
        assert _marks(listener) == {"applications"}


# -- MCP --------------------------------------------------------------------

def test_an_mcp_server_added_or_removed_signals_its_name() -> None:
    from openprogram.extensions.mcp import registry
    from openprogram.extensions.mcp.config import MCPServerConfig
    cfg = MCPServerConfig(name="topic-test", type="local", command=["true"], enabled=False)
    with changes.listen("mcp") as listener:
        asyncio.run(registry._spawn_and_register(cfg))
        assert _marks(listener) == {"mcp:topic-test"}
        asyncio.run(registry._stop_and_unregister("topic-test"))
        assert _marks(listener) == {"mcp:topic-test"}


# -- meetings ---------------------------------------------------------------

def test_a_meeting_writing_its_status_signals_its_id(tmp_path) -> None:
    from openprogram.programs.applications.meeting import session
    meeting = session.Meeting.__new__(session.Meeting)
    meeting.state = "transcribing"
    meeting.directory = tmp_path
    meeting.id = "m1"
    meeting.meta = lambda: {"id": "m1"}
    with changes.listen("meetings") as listener:
        meeting._write_meta()
        assert _marks(listener) == {"meetings:m1"}
