from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState


def _app() -> OwnerAuthMiddleware:
    async def html(_request: Request) -> HTMLResponse:
        return HTMLResponse("<script>window.ready=true</script>")

    app = Starlette(
        routes=[
            Route("/docs/reference/design/ui/gui-agent.html", html),
            Route("/docs/reference/design/ui/gui-agent.raw.html", html),
        ]
    )
    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
    )
    return OwnerAuthMiddleware(app, auth_state=state)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
def test_raw_design_html_allows_only_same_origin_embedding(method: str) -> None:
    with TestClient(_app(), base_url="http://127.0.0.1:18100") as client:
        response = client.request(
            method, "/docs/reference/design/ui/gui-agent.raw.html"
        )

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]


def test_rendered_design_html_remains_non_embeddable() -> None:
    with TestClient(_app(), base_url="http://127.0.0.1:18100") as client:
        response = client.get("/docs/reference/design/ui/gui-agent.html")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
