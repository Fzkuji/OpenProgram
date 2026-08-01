import inspect
import json

import pytest
import client as C
from client import ApiClient, ApiError


def test_urllib_transport_exists():
    assert callable(C.urllib_transport)
    assert "transport" in inspect.signature(ApiClient.__init__).parameters


def test_default_transport_is_urllib():
    assert ApiClient("http://x").transport is C.urllib_transport


def test_get_uses_transport():
    calls = []

    def t(method, url, body):
        calls.append((method, url, body))
        return 200, json.dumps({"ok": True})

    assert ApiClient("http://x/", transport=t).get("/a") == {"ok": True}
    assert calls == [("GET", "http://x/a", None)]


def test_post_body():
    seen = {}

    def t(method, url, body):
        seen.update(method=method, body=body)
        return 201, json.dumps({"id": 1})

    assert ApiClient("http://x", transport=t).post("/a", {"k": 1}) == {"id": 1}
    assert seen == {"method": "POST", "body": {"k": 1}}


def test_retry_on_5xx():
    n = []

    def t(method, url, body):
        n.append(1)
        if len(n) < 3:
            return 503, ""
        return 200, json.dumps({"ok": 1})

    assert ApiClient("http://x", transport=t).get("/a") == {"ok": 1}
    assert len(n) == 3


def test_retry_exhausted():
    n = []

    def t(method, url, body):
        n.append(1)
        return 500, ""

    with pytest.raises(ApiError):
        ApiClient("http://x", transport=t).get("/a")
    assert len(n) == 3


def test_4xx_not_retried():
    n = []

    def t(method, url, body):
        n.append(1)
        return 404, ""

    with pytest.raises(ApiError):
        ApiClient("http://x", transport=t).get("/a")
    assert len(n) == 1
