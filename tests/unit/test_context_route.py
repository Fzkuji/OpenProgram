"""GET /api/sessions/{id}/context — 现算当前会话的 input token 分类分解。

mock default_db 返回一条带 tools_available（藏在 extra JSON 里）的分支，
断言端点解出工具集并算出 breakdown（分类 + per-tool）。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeDB:
    def get_branch(self, session_id):
        return [
            {"role": "user", "content": "研究一下 X 主题", "extra": None},
            {
                "role": "llm",
                "content": "好的，我来研究",
                "extra": json.dumps({
                    "tool_calls": [],
                    "blocks": [],
                    "tools_available": ["bash", "read", "web_search"],
                    "system_prompt": "You are a research agent. Be thorough and cite sources.",
                }),
            },
        ]

    def get_session(self, session_id):
        return {"model": ""}


@pytest.fixture
def client(monkeypatch):
    import openprogram.agent.session_db as _sdb
    monkeypatch.setattr(_sdb, "default_db", lambda: _FakeDB())

    app = FastAPI()
    from openprogram.webui.routes import tree as _tree
    _tree.register(app)
    return TestClient(app)


def test_context_endpoint_returns_breakdown(client):
    r = client.get("/api/sessions/s1/context")
    assert r.status_code == 200
    d = r.json()
    assert "error" not in d, d.get("error")
    # 工具集从 extra.tools_available 解出
    names = {t["name"] for t in d["tools"]}
    assert names == {"bash", "read", "web_search"}
    # 分类字段齐、总量为正
    assert d["messages"] > 0
    assert d["system_prompt"] > 0   # system 类真实值（存了原料，不再是假 0）
    assert d["input_used"] > 0
    assert d["session_id"] == "s1"


def test_context_endpoint_no_tools(client, monkeypatch):
    import openprogram.agent.session_db as _sdb

    class _EmptyDB:
        def get_branch(self, sid):
            return [{"role": "user", "content": "hi", "extra": None}]

        def get_session(self, sid):
            return {"model": ""}

    monkeypatch.setattr(_sdb, "default_db", lambda: _EmptyDB())
    r = client.get("/api/sessions/s2/context")
    assert r.status_code == 200
    d = r.json()
    assert d["tools"] == []
    assert d["tools_schema"] == 0
