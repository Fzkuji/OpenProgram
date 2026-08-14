"""The public health probe is non-identifying and needs no credential."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openprogram.webui.server import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:18100") as c:
        yield c


def test_healthz_is_minimal_and_public(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"
