"""``/healthz`` liveness/readiness probe.

Used by load balancers and humans curl-checking the server. Reports
SessionDB connectivity, registered tool count, recent activity, and
uptime. Returns 200 always — body's ``status`` field tells the
caller whether the system is "ok" or "degraded".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.agent.session_db import SessionDB
from openprogram.webui.messages import MessageStore, set_store_for_testing
from openprogram.webui.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    set_store_for_testing(MessageStore(persist_dir=tmp_path / "store"))
    app = create_app()
    with TestClient(app) as c:
        yield c, db
    set_store_for_testing(None)


def test_healthz_reports_ok_when_db_responds(client) -> None:
    c, db = client
    db.create_session("c1", "main", title="t")
    db.append_message("c1", {
        "id": "m1", "role": "user", "content": "x",
        "timestamp": __import__("time").time(), "parent_id": None,
    })

    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert body["sessions_visible"] >= 1
    assert body["messages_24h"] >= 1
    assert body["tools_registered"] >= 20    # every built-in tool
    assert "uptime_seconds" in body


def test_healthz_includes_tool_count(client) -> None:
    c, _ = client
    body = c.get("/healthz").json()
    # Every built-in dict tool gets auto-wrapped at registry import
    # (tools/__init__.py:_autoload_agent_registry). Drop here would
    # mean some tool's @tool decorator isn't firing on import.
    assert body["tools_registered"] >= 20


def test_healthz_returns_200_even_when_degraded(
    client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the DB ping raises (broken file / missing perms / replaced
    schema), healthz should still return 200 with ``status=degraded``
    so the LB / human can read the body and decide. Returning 503 is a
    deployment policy choice — we leave that to the consumer."""
    c, _ = client

    # Force the db ping to fail by patching default_db to raise
    def _broken():
        raise RuntimeError("simulated db fault")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        _broken)

    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db_ok"] is False
    assert "simulated db fault" in body["db_error"]
