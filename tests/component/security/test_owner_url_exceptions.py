from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openprogram import config_schema
from openprogram.security.safe_http import (
    OutboundSecurityConfig,
    PolicyProxyConfig,
    configured_safe_client,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException
from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
from openprogram.webui.routes import config as config_routes
from openprogram.webui.ws_actions.settings import handle_set_setting


VALID_SETTINGS = {
    "exceptions": [
        {
            "consumer": "skills.configured.catalog",
            "origin": "HTTPS://Catalog.Corp.Example:8443",
        },
        {
            "consumer": "provider.configured_api",
            "cidr": "10.20.7.9/16",
        },
    ],
    "policy_proxy": {
        "url": "http://127.0.0.1:3128",
        "enforces_target_policy": True,
    },
}


def test_owner_outbound_url_settings_are_strict_normalized_and_immutable():
    settings = config_schema.parse_outbound_url_settings(VALID_SETTINGS)

    assert settings.model_dump(mode="json", exclude_none=True) == {
        "exceptions": [
            {
                "consumer": "skills.configured.catalog",
                "origin": "https://catalog.corp.example:8443",
            },
            {
                "consumer": "provider.configured_api",
                "cidr": "10.20.0.0/16",
            },
        ],
        "policy_proxy": {
            "url": "http://127.0.0.1:3128",
            "enforces_target_policy": True,
        },
    }
    with pytest.raises(Exception):
        settings.exceptions = ()


def test_owner_outbound_url_settings_reject_ipv4_mapped_link_local_origin():
    with pytest.raises(ValueError, match="invalid outbound URL security"):
        config_schema.parse_outbound_url_settings(
            {
                "exceptions": [
                    {
                        "consumer": "skills.configured.catalog",
                        "origin": "http://[::ffff:169.254.1.2]",
                    }
                ]
            }
        )


def test_owner_outbound_url_settings_build_consumer_scoped_security():
    settings = config_schema.parse_outbound_url_settings(VALID_SETTINGS)

    skills = settings.security_for("skills.configured.catalog")
    assert skills.owner_exceptions == (
        OwnerURLException(
            consumer="skills.configured.catalog",
            origin="https://catalog.corp.example:8443",
        ),
        OwnerURLException(
            consumer="runtime.local_probe",
            origin="http://127.0.0.1:3128",
        ),
    )
    assert skills.policy_proxy == PolicyProxyConfig(
        url="http://127.0.0.1:3128",
        enforces_target_policy=True,
    )

    public = settings.security_for("tool.web_fetch")
    assert public.owner_exceptions == (
        OwnerURLException(
            consumer="runtime.local_probe",
            origin="http://127.0.0.1:3128",
        ),
    )
    assert public.policy_proxy == skills.policy_proxy


@pytest.mark.parametrize(
    "value",
    [
        {
            "exceptions": [
                {"consumer": "unknown.consumer", "origin": "https://ok.example"}
            ]
        },
        {
            "exceptions": [
                {"consumer": "tool.web_fetch", "origin": "https://ok.example"}
            ]
        },
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://*.example.com",
                }
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "origin": ".example.com"}
            ]
        },
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://user:secret@example.com",
                }
            ]
        },
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://example.com/private?token=QUERY-SECRET",
                }
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "0.0.0.0/0"}
            ]
        },
        {"exceptions": [{"consumer": "skills.configured.catalog", "cidr": "::/0"}]},
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "169.254.0.0/16"}
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "fe80::/10"}
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "169.254.169.254/32"}
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "8.8.8.0/24"}
            ]
        },
        {
            "exceptions": [
                {"consumer": "skills.configured.catalog", "cidr": "224.0.0.0/4"}
            ]
        },
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "http://169.254.169.254",
                }
            ]
        },
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://metadata.google.internal",
                }
            ]
        },
        {"exceptions": [{"consumer": "skills.configured.catalog"}]},
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://ok.example",
                    "extra": True,
                }
            ]
        },
        {"exceptions": [], "unknown": True},
        {
            "policy_proxy": {
                "url": "http://127.0.0.1:3128",
                "enforces_target_policy": False,
            }
        },
        {"policy_proxy": {"url": "http://127.0.0.1:3128", "enforces_target_policy": 1}},
        {
            "policy_proxy": {
                "url": "http://user:secret@127.0.0.1:3128",
                "enforces_target_policy": True,
            }
        },
        {
            "policy_proxy": {
                "url": "http://127.0.0.1:3128/path?token=QUERY-SECRET",
                "enforces_target_policy": True,
            }
        },
    ],
)
def test_owner_outbound_url_settings_reject_invalid_or_broad_authority(value):
    with pytest.raises(ValueError) as exc_info:
        config_schema.parse_outbound_url_settings(value)

    message = str(exc_info.value)
    assert "QUERY-SECRET" not in message
    assert "secret" not in message


def test_loaded_owner_settings_default_fail_closed_on_invalid_persisted_config():
    config = {
        "security": {
            "outbound_url": {
                "exceptions": [
                    {
                        "consumer": "skills.configured.catalog",
                        "cidr": "0.0.0.0/0",
                    }
                ]
            }
        }
    }

    with pytest.raises(ValueError, match="invalid outbound URL security configuration"):
        config_schema.load_outbound_security_config(
            "skills.configured.catalog", config=config
        )

    with pytest.raises(ValueError, match="invalid outbound URL security configuration"):
        config_schema.load_outbound_security_config(
            "skills.configured.catalog", config={"security": []}
        )


def test_owner_exception_batch_is_bounded_and_duplicate_free():
    too_many = {
        "exceptions": [
            {
                "consumer": "skills.configured.catalog",
                "origin": f"https://catalog-{index}.corp.example",
            }
            for index in range(65)
        ]
    }
    duplicate = {
        "exceptions": [
            {
                "consumer": "skills.configured.catalog",
                "origin": "https://catalog.corp.example",
            },
            {
                "consumer": "skills.configured.catalog",
                "origin": "HTTPS://CATALOG.CORP.EXAMPLE",
            },
        ]
    }

    with pytest.raises(ValueError, match="invalid outbound URL security"):
        config_schema.parse_outbound_url_settings(too_many)
    with pytest.raises(ValueError, match="invalid outbound URL security"):
        config_schema.parse_outbound_url_settings(duplicate)


def test_managed_factories_load_owner_policy_without_minting_callsite_authority(
    monkeypatch,
):
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {
            "security": {
                "outbound_url": {
                    "exceptions": [
                        {
                            "consumer": "skills.configured.catalog",
                            "origin": "http://127.0.0.1:18181",
                        }
                    ],
                    "policy_proxy": {
                        "url": "http://127.0.0.1:3128",
                        "enforces_target_policy": True,
                    },
                }
            }
        },
    )

    with configured_safe_client(
        "skills.configured.catalog", "http://127.0.0.1:18181"
    ) as configured:
        assert configured._transport._security.owner_exceptions == (
            OwnerURLException(
                consumer="skills.configured.catalog",
                origin="http://127.0.0.1:18181",
            ),
            OwnerURLException(
                consumer="runtime.local_probe",
                origin="http://127.0.0.1:3128",
            ),
        )

    with safe_client("tool.web_fetch") as public:
        assert public._transport._security.policy_proxy == PolicyProxyConfig(
            url="http://127.0.0.1:3128",
            enforces_target_policy=True,
        )


def test_explicit_test_security_does_not_read_owner_config(monkeypatch):
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: (_ for _ in ()).throw(AssertionError("owner config read")),
    )

    with safe_client("tool.web_fetch", security=OutboundSecurityConfig()) as client:
        assert client._transport._security == OutboundSecurityConfig()


def test_existing_web_config_write_path_requires_owner_authentication(monkeypatch):
    writes = []
    monkeypatch.setattr(
        "openprogram.setup.update_config",
        lambda mutator: writes.append(mutator),
    )
    app = FastAPI()
    config_routes.register(app)
    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
    )
    protected = OwnerAuthMiddleware(app, auth_state=state)
    body = {
        "key": "security.outbound_url",
        "value": {"exceptions": []},
    }

    with TestClient(protected, base_url="http://127.0.0.1:18100") as client:
        rejected = client.post("/api/settings", json=body)
        accepted = client.post(
            "/api/settings",
            headers={"Authorization": f"Bearer {state.token}"},
            json=body,
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert len(writes) == 1


def test_existing_websocket_setting_path_requires_owner_authentication(monkeypatch):
    writes = []
    monkeypatch.setattr(
        "openprogram.setup.update_config",
        lambda mutator: writes.append(mutator),
    )
    app = FastAPI()

    @app.websocket("/ws")
    async def settings_socket(ws: WebSocket):
        await ws.accept()
        await handle_set_setting(ws, await ws.receive_json())
        await ws.close()

    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
    )
    protected = OwnerAuthMiddleware(app, auth_state=state)
    command = {
        "key": "security.outbound_url",
        "value": {"exceptions": []},
    }

    with TestClient(protected, base_url="http://127.0.0.1:18100") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass
        with client.websocket_connect(
            "/ws",
            headers={
                "Authorization": f"Bearer {state.token}",
                "Host": "127.0.0.1:18100",
            },
        ) as ws:
            ws.send_json(command)
            result = ws.receive_json()

    assert result["type"] == "setting_result"
    assert result["data"]["applied"] == "live"
    assert len(writes) == 1
