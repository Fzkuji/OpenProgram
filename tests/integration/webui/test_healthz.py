"""The public health probe is non-identifying and needs no credential."""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.webui.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:18100") as c:
        yield c


def test_healthz_is_minimal_and_public(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_static_shell_csp_authorizes_only_its_built_inline_scripts(client) -> None:
    response = client.get("/chat")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    scripts = [
        body.encode()
        for attributes, body in re.findall(
            r"<script([^>]*)>(.*?)</script\s*>",
            response.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not re.search(r"\bsrc\s*=", attributes, re.IGNORECASE)
    ]
    assert scripts
    for script in scripts:
        digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert "'unsafe-inline'" not in policy
