from openprogram.config_schema import set_setting


def test_allowed_origins_are_stored_as_a_validated_json_list(monkeypatch):
    saved = []

    def capture_update(mutator):
        config = {}
        mutator(config)
        saved.append(config)

    monkeypatch.setattr("openprogram.setup.update_config", capture_update)

    result = set_setting(
        "web.allowed_origins",
        '["https://agent.example.com", "http://192.168.1.20:18100"]',
    )

    assert result["value"] == [
        "https://agent.example.com",
        "http://192.168.1.20:18100",
    ]
    assert saved == [{"web": {"allowed_origins": result["value"]}}]


def test_allowed_origins_reject_paths_before_persistence(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "openprogram.setup.update_config",
        lambda mutator: saved.append(mutator),
    )

    result = set_setting(
        "web.allowed_origins",
        '["https://agent.example.com/path"]',
    )

    assert "error" in result
    assert saved == []


def test_resource_limit_settings_accept_positive_or_null(monkeypatch):
    saved = []

    def capture_update(mutator):
        config = {}
        mutator(config)
        saved.append(config)

    monkeypatch.setattr("openprogram.setup.update_config", capture_update)

    assert set_setting("agent.resource_limits.max_total_tokens", "12")["value"] == 12
    assert set_setting("agent.resource_limits.max_cost_usd", "2.50")["value"] == "2.50"
    assert set_setting("agent.resource_limits.max_total_tokens", "")["value"] is None
    assert saved == [
        {"agent": {"resource_limits": {"max_total_tokens": 12}}},
        {"agent": {"resource_limits": {"max_cost_usd": "2.50"}}},
        {"agent": {"resource_limits": {"max_total_tokens": None}}},
    ]


def test_resource_limit_settings_reject_zero(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "openprogram.setup.update_config", lambda mutator: saved.append(mutator),
    )

    assert "error" in set_setting("agent.resource_limits.max_live_per_session", 0)
    assert "error" in set_setting("agent.resource_limits.max_cost_usd", "0")
    assert saved == []


def test_outbound_url_security_is_normalized_before_atomic_persistence(monkeypatch):
    saved = []

    def capture_update(mutator):
        config = {"unrelated": {"preserved": True}}
        mutator(config)
        saved.append(config)

    monkeypatch.setattr("openprogram.setup.update_config", capture_update)
    result = set_setting(
        "security.outbound_url",
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "HTTPS://Catalog.Corp.Example:8443",
                }
            ],
            "policy_proxy": {
                "url": "http://127.0.0.1:3128",
                "enforces_target_policy": True,
            },
        },
    )

    assert result == {
        "applied": "live",
        "value": {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://catalog.corp.example:8443",
                }
            ],
            "policy_proxy": {
                "url": "http://127.0.0.1:3128",
                "enforces_target_policy": True,
            },
        },
    }
    assert saved == [
        {
            "unrelated": {"preserved": True},
            "security": {"outbound_url": result["value"]},
        }
    ]


def test_outbound_url_security_rejection_is_sanitized_and_does_not_write(monkeypatch):
    writes = []
    monkeypatch.setattr("openprogram.setup.update_config", writes.append)

    result = set_setting(
        "security.outbound_url",
        {
            "exceptions": [
                {
                    "consumer": "skills.configured.catalog",
                    "origin": "https://user:BEARER-TOKEN@example.com/path?token=QUERY-SECRET",
                }
            ]
        },
    )

    assert result == {
        "error": "Outbound URL security: invalid outbound URL security configuration"
    }
    assert "BEARER-TOKEN" not in repr(result)
    assert "QUERY-SECRET" not in repr(result)
    assert writes == []


def test_outbound_url_malformed_json_error_does_not_reflect_input(monkeypatch):
    writes = []
    monkeypatch.setattr("openprogram.setup.update_config", writes.append)

    result = set_setting(
        "security.outbound_url",
        '{"policy_proxy":{"url":"http://user:BEARER-TOKEN@127.0.0.1?token=QUERY-SECRET"',
    )

    assert result == {
        "error": "Outbound URL security: invalid outbound URL security configuration"
    }
    assert "BEARER-TOKEN" not in repr(result)
    assert "QUERY-SECRET" not in repr(result)
    assert writes == []
