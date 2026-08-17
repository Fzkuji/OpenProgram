from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from openprogram.webui.server import create_app


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_static_shell_csp_authorizes_only_its_built_inline_scripts() -> None:
    built_chat = ROOT / "apps/web/out/chat.html"
    assert built_chat.is_file(), "build the Web package before running e2e tests"

    with TestClient(create_app(), base_url="http://127.0.0.1:18100") as client:
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
