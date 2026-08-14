"""REST 端点 /api/questions（list / reply / reject）—— user-input 重连恢复。

WS 是活路径；这几个 REST 端点是同一 registry 的 API 对等，供非 WS 客户端
/ 重连客户端枚举和应答待答问题。reply/reject 走与 WS 同一个收口
（_resolve_question），所以"第一个答复者赢"跨 transport 成立。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import openprogram.agent.questions as Q
from openprogram.agent.questions import QuestionRegistry, PendingQuestion
from openprogram.webui.routes import questions as _routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    app = FastAPI()
    _routes.register(app)
    return TestClient(app)


def _seed(qid, session_id="s", kind="ask", prompt="lib?", options=None):
    Q.get_question_registry().register(PendingQuestion(
        id=qid, session_id=session_id, kind=kind, prompt=prompt,
        options=options or []))


def test_list_all(client):
    _seed("a", session_id="s1")
    _seed("b", session_id="s2")
    r = client.get("/api/questions")
    assert r.status_code == 200
    ids = {q["id"] for q in r.json()["questions"]}
    assert ids == {"a", "b"}


def test_list_filtered_by_session(client):
    _seed("a", session_id="s1")
    _seed("b", session_id="s2")
    r = client.get("/api/questions", params={"session_id": "s1"})
    qs = r.json()["questions"]
    assert [q["id"] for q in qs] == ["a"]
    assert qs[0]["prompt"] == "lib?"


def test_form_schema_survives_rest_recovery(client):
    """A kind="form" question must expose its field schema over REST so a
    reconnecting client can redraw the multi-field form, not an empty box."""
    fields = {"name": {"type": "string"}, "mode": {"type": "string", "enum": ["a", "b"]}}
    Q.get_question_registry().register(PendingQuestion(
        id="f1", session_id="s", kind="form", prompt="配置", schema=fields))
    r = client.get("/api/questions", params={"session_id": "s"})
    q = next(x for x in r.json()["questions"] if x["id"] == "f1")
    assert q["kind"] == "form"
    assert q["schema"] == fields


def test_reply_resolves_registry(client):
    _seed("a", options=["x", "y"])
    ev = Q.get_question_registry()._events["a"]
    r = client.post("/api/questions/a/reply", json={"answer": "y"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert ev.is_set()
    assert Q.get_question_registry().consume("a") == ("answered", "y")


def test_reject_resolves_declined(client):
    _seed("a")
    r = client.post("/api/questions/a/reject")
    assert r.status_code == 200
    assert Q.get_question_registry().consume("a") == ("declined", None)


def test_reply_unknown_id_is_noop(client):
    # 不存在的 id：收口 resolve 返回 False，端点仍 200（幂等、无副作用）。
    r = client.post("/api/questions/nope/reply", json={"answer": "z"})
    assert r.status_code == 200


def test_reply_claim_once(client):
    _seed("a")
    client.post("/api/questions/a/reply", json={"answer": "first"})
    # 第二次答复：claim-once，resolve 返回 False，结果仍是第一个。
    client.post("/api/questions/a/reply", json={"answer": "second"})
    assert Q.get_question_registry().consume("a") == ("answered", "first")
