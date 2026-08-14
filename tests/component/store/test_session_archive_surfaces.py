"""The CLI and REST surfaces of session archiving, over a temp store."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.store.session.session_store import SessionStore

NOW = time.time()


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    """A temp store standing in for the process-wide ``default_db()``."""
    s = SessionStore(tmp_path / "sessions")
    s.create_session("alpha", "main", title="Alpha", updated_at=NOW - 60)
    s.create_session("beta", "main", title="Beta", updated_at=NOW)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: s)
    return s


# CLI


def test_cli_archive_then_unarchive_round_trips(store, capsys) -> None:
    from openprogram._cli_cmds.sessions import _cmd_session_archive

    _cmd_session_archive("beta", True)
    assert "Archived session beta" in capsys.readouterr().out
    assert [r["id"] for r in store.list_sessions()] == ["alpha"]

    _cmd_session_archive("beta", False)
    assert "Unarchived session beta" in capsys.readouterr().out
    assert [r["id"] for r in store.list_sessions()] == ["beta", "alpha"]


def test_cli_archive_of_an_unknown_session_exits_nonzero(store, capsys) -> None:
    from openprogram._cli_cmds.sessions import _cmd_session_archive

    with pytest.raises(SystemExit) as exc:
        _cmd_session_archive("nope", True)

    assert exc.value.code == 1
    assert "no session" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("scope", "expected"),
    [("active", ["alpha"]), ("archived", ["beta"]), ("all", ["beta", "alpha"])],
)
def test_cli_list_scopes(store, capsys, scope, expected) -> None:
    from openprogram._cli_cmds.sessions import _cmd_chat_sessions

    store.set_archived("beta", True)
    _cmd_chat_sessions(scope)

    out = capsys.readouterr().out
    listed = [line.split()[0] for line in out.splitlines()
              if line.startswith("  ")]
    assert listed == expected


def test_cli_parser_exposes_archive_verbs() -> None:
    """The verbs have to reach the dispatch, not just exist as functions."""
    from openprogram.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["sessions", "archive", "beta"])
    assert args.sessions_verb == "archive"
    assert args.session_id == "beta"

    args = parser.parse_args(["sessions", "list", "--chat", "--archived"])
    assert args.chat and args.archived


# REST


@pytest.fixture()
def client(store, monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.webui import server as _s
    from openprogram.webui.routes import chat as chat_routes

    monkeypatch.setattr(_s, "_sessions", {}, raising=False)
    monkeypatch.setattr(_s, "_broadcast", lambda *a, **k: None, raising=False)

    app = FastAPI()
    chat_routes.register(app)
    return TestClient(app)


def test_archive_endpoint_hides_the_session(client, store) -> None:
    r = client.post("/api/sessions/archive", json={"session_id": "beta"})

    assert r.status_code == 200
    assert r.json() == {"session_id": "beta", "archived": True}
    assert [x["id"] for x in store.list_sessions()] == ["alpha"]


def test_unarchive_endpoint_restores_the_session(client, store) -> None:
    store.set_archived("beta", True)

    r = client.post("/api/sessions/unarchive", json={"session_id": "beta"})

    assert r.status_code == 200
    assert r.json() == {"session_id": "beta", "archived": False}
    assert [x["id"] for x in store.list_sessions()] == ["beta", "alpha"]


def test_archive_endpoint_preserves_activity_time(client, store) -> None:
    client.post("/api/sessions/archive", json={"session_id": "beta"})

    assert store.get_session("beta")["updated_at"] == pytest.approx(NOW)
    assert store.list_sessions(archived=True)[0]["updated_at"] == pytest.approx(NOW)


def test_archive_endpoint_rejects_a_missing_session_id(client) -> None:
    assert client.post("/api/sessions/archive", json={}).status_code == 400


def test_archive_endpoint_reports_an_unknown_session(client) -> None:
    r = client.post("/api/sessions/archive", json={"session_id": "nope"})

    assert r.status_code == 404
