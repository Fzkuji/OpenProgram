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
