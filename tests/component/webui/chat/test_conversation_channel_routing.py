"""Picking a channel account on a chat ties the chat to that account's
conversation both ways.

The channel chip sends ``set_conversation_channel`` with a channel and an
account but no peer. For WeChat the person talking to the bot is the
account owner (``ilink_user_id``), so the binding writes a session alias
for that peer, and inbound routing (alias before binding) delivers the
owner's messages into the chat instead of the ``session_scope`` session.
Switching back to Local or to another account removes the alias, and only
one chat holds a peer's conversation at a time.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _conversation as C
from openprogram.interfaces.channels import accounts as _accounts
from openprogram.interfaces.server.ws_actions.chat import conversation_channel as CC
from openprogram.runtime.agent_registry import session_aliases as A
from openprogram.runtime.context.session_db import SessionDB

OWNER = "o9cq80y8K9j5JBUISailVBX4ZvkE@im.wechat"
BABA_OWNER = "o9cq80wMHqYhx7ZsCK2l4kNnFLbM@im.wechat"


def _write_creds(state: Path, account: str, user_id: str) -> None:
    path = state / "channels" / "wechat" / "accounts" / account / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bot_token": "t", "ilink_bot_id": "b",
                                "ilink_user_id": user_id}), encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    _write_creds(state, "default", OWNER)
    _write_creds(state, "baba", BABA_OWNER)
    server = SimpleNamespace(
        _sessions={},
        _sessions_lock=threading.RLock(),
        _default_agent_id=lambda: "main",
        _log=lambda *_a, **_k: None,
    )
    for sid in ("local_app", "local_other"):
        db.create_session(sid, "main", title=sid)
        server._sessions[sid] = {"id": sid, "agent_id": "main"}
    monkeypatch.setattr(C._agents, "get", lambda agent_id: object())
    return server, db


def _route(account: str, peer_id: str):
    return C._route("wechat", account, {"kind": "direct", "id": peer_id})


def test_owner_peer_is_the_wechat_user_who_scanned_the_qr(env) -> None:
    assert _accounts.owner_peer_id("wechat", "default") == OWNER
    assert _accounts.owner_peer_id("telegram", "default") == ""


def test_picking_wechat_routes_the_owner_chat_into_this_chat(env) -> None:
    server, db = env

    result = CC.bind_conversation_channel(server, "local_app", "wechat", "default")

    assert result.ok and result.peer == OWNER
    assert _route("default", OWNER) == ("main", "local_app", None)
    sess = db.get_session("local_app")
    assert (sess["channel"], sess["account_id"]) == ("wechat", "default")


def test_switching_to_local_falls_back_to_the_binding(env, monkeypatch) -> None:
    server, db = env
    monkeypatch.setattr(C._bindings, "route", lambda *a: "main")
    monkeypatch.setattr(C, "_session_key_for_agent", lambda *a: "default_direct_owner")
    monkeypatch.setattr(C, "_apply_reset_policy", lambda agent, key: key)
    CC.bind_conversation_channel(server, "local_app", "wechat", "default")

    result = CC.bind_conversation_channel(server, "local_app", None, None)

    assert result.ok
    assert A.list_all() == []
    assert _route("default", OWNER) == ("main", "default_direct_owner", "default_direct_owner")
    # Unbinding persists: the chat no longer counts as bound after a reload.
    assert not db.get_session("local_app").get("channel")
    assert db.sessions_with_binding("wechat", "default") == []


def test_switching_account_moves_the_alias(env) -> None:
    server, _db = env
    CC.bind_conversation_channel(server, "local_app", "wechat", "default")

    CC.bind_conversation_channel(server, "local_app", "wechat", "baba")

    rows = A.list_all()
    assert [(r["account_id"], r["peer"]["id"]) for r in rows] == [("baba", BABA_OWNER)]
    assert _route("baba", BABA_OWNER)[1] == "local_app"


def test_one_chat_holds_an_account_conversation(env) -> None:
    server, db = env
    CC.bind_conversation_channel(server, "local_other", "wechat", "default")

    result = CC.bind_conversation_channel(server, "local_app", "wechat", "default")

    assert result.evicted == ["local_other"]
    assert [r["session_id"] for r in A.list_all()] == ["local_app"]
    assert not db.get_session("local_other").get("channel")
    assert _route("default", OWNER)[1] == "local_app"


def _telegram(server, session_id: str, peer: str | None = None):
    return CC.bind_conversation_channel(server, session_id, "telegram", "work", peer)


def test_chats_with_different_peers_share_an_account(env) -> None:
    server, db = env
    _telegram(server, "local_other", "alice")

    result = _telegram(server, "local_app", "bob")

    assert result.ok and result.evicted == []
    assert db.get_session("local_other")["channel"] == "telegram"
    assert server._sessions["local_other"]["peer"] == "alice"
    assert A.lookup("telegram", "work", {"kind": "direct", "id": "alice"})[1] == "local_other"
    assert A.lookup("telegram", "work", {"kind": "direct", "id": "bob"})[1] == "local_app"


def test_taking_the_same_peer_evicts_its_chat(env) -> None:
    server, db = env
    _telegram(server, "local_other", "alice")

    result = _telegram(server, "local_app", "alice")

    assert result.evicted == ["local_other"]
    assert not db.get_session("local_other").get("channel")
    assert [r["session_id"] for r in A.list_all()] == ["local_app"]


def test_peerless_binding_leaves_one_owner(env) -> None:
    server, db = env
    _telegram(server, "local_other")

    result = _telegram(server, "local_app", "alice")

    assert result.evicted == ["local_other"]
    assert not db.get_session("local_other").get("channel")


def test_new_peerless_binding_takes_the_whole_account(env) -> None:
    server, db = env
    _telegram(server, "local_other", "alice")

    result = _telegram(server, "local_app")

    assert result.evicted == ["local_other"]
    assert A.list_all() == []


def test_owner_pick_keeps_a_conversation_that_arrived_from_someone_else(env) -> None:
    server, db = env
    db.create_session("arrived", "main", title="friend", channel="wechat",
                      source="wechat", account_id="default",
                      peer_kind="direct", peer_id="friend@im.wechat",
                      peer={"kind": "direct", "id": "friend@im.wechat"})

    result = CC.bind_conversation_channel(server, "local_app", "wechat", "default")

    assert result.evicted == []
    assert db.get_session("arrived")["channel"] == "wechat"


def test_catch_all_alias_counts_as_the_whole_account(env) -> None:
    server, db = env
    _telegram(server, "local_other")
    A.attach(channel="telegram", account_id="work", peer_kind="direct",
             peer_id="*", agent_id="main", session_id="local_other")

    result = _telegram(server, "local_app", "alice")

    assert result.evicted == ["local_other"]
    assert [r["session_id"] for r in A.list_all()] == ["local_app"]


def test_chat_with_several_peers_loses_only_the_one_taken(env) -> None:
    server, _db = env
    _telegram(server, "local_other", "alice")
    A.attach(channel="telegram", account_id="work", peer_kind="direct",
             peer_id="carol", agent_id="main", session_id="local_other")

    result = _telegram(server, "local_app", "alice")

    assert result.evicted == []
    assert A.lookup("telegram", "work", {"kind": "direct", "id": "alice"})[1] == "local_app"
    assert A.lookup("telegram", "work", {"kind": "direct", "id": "carol"})[1] == "local_other"


def test_chat_left_with_another_peer_stops_naming_the_one_taken(env) -> None:
    from openprogram.interfaces.channels._question_bridge import _channel_target_for_session

    server, db = env
    _telegram(server, "local_other", "carol")
    A.attach(channel="telegram", account_id="work", peer_kind="direct",
             peer_id="alice", agent_id="main", session_id="local_other")
    server._sessions["local_other"]["peer"] = "alice"
    db.update_session("local_other", peer_display="Alice")
    assert _channel_target_for_session("local_other") == ("telegram", "work", "alice")

    result = _telegram(server, "local_app", "alice")

    assert result.evicted == []
    assert _channel_target_for_session("local_other") == ("telegram", "work", "carol")
    assert server._sessions["local_other"]["peer"] == "carol"
    assert not db.get_session("local_other").get("peer_display")


def test_platform_without_owner_records_binding_only(env) -> None:
    server, db = env

    result = CC.bind_conversation_channel(server, "local_app", "telegram", "work")

    assert result.ok and result.peer is None
    assert A.list_all() == []
    assert db.get_session("local_app")["channel"] == "telegram"


def test_restore_gives_chip_bound_chats_their_alias(env) -> None:
    server, db = env
    # Bound by the chip before it wrote aliases: channel + account, no peer.
    db.update_session("local_app", channel="wechat", account_id="default", peer=None)
    # A conversation that arrived from the channel records its peer.
    db.create_session("default_direct_x_n1", "main", title="routed",
                      channel="wechat", source="wechat", account_id="default",
                      peer_kind="direct", peer_id=OWNER,
                      peer={"kind": "direct", "id": OWNER})

    assert CC.restore_channel_aliases(server) == 1
    assert _route("default", OWNER)[1] == "local_app"
    assert CC.restore_channel_aliases(server) == 0
