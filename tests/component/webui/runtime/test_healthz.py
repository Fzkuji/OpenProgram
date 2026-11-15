"""The public health probe names only the product release and needs no credential."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openprogram.interfaces.server.server import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:18100") as c:
        yield c


def test_healthz_is_minimal_and_public(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    payload = r.json()
    assert payload.pop("version")
    assert payload == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"
