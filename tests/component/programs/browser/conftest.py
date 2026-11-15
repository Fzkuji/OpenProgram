"""Explicit owned Page context for controller-based browser task tests."""
import pytest


@pytest.fixture
def owned_page_scope(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    token = surface_context.bind({"primary_surface_key": "fixture-page", "surfaces": [
        {"surface_key": "fixture-page", "binding_id": "fixture-binding", "capabilities": ["observe"]},
    ]})
    monkeypatch.setattr(webtab, "request_bound_tab", lambda binding_id, **_kwargs: {
        "ok": binding_id == "fixture-binding", "input_scale": 1,
    })
    import base64
    monkeypatch.setattr(webtab, "request_bound_screenshot", lambda binding_id, **_kwargs: {
        "ok": binding_id == "fixture-binding",
        "image_data_url": "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode(),
    })
    try:
        yield
    finally:
        surface_context.reset(token)
