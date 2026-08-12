from openprogram.config_schema import get_settings, set_setting


def _setting(key: str) -> dict:
    return next(row for row in get_settings() if row["key"] == key)


def test_mcp_exposed_tools_missing_config_is_default_empty(monkeypatch):
    monkeypatch.setattr("openprogram.setup._read_config", lambda: {})

    row = _setting("mcp_server.exposed_tools")

    assert row == {
        "key": "mcp_server.exposed_tools",
        "group": "MCP server",
        "label": "Exposed Runtime tools",
        "widget": "json",
        "apply": "next_start",
        "help": (
            "Runtime tools available to authenticated MCP clients. Empty by "
            "default; changes apply on the next server start."
        ),
        "value": "[]",
    }


def test_mcp_exposed_tools_validates_before_persistence(monkeypatch):
    saved = []

    def capture_update(mutator):
        config = {}
        mutator(config)
        saved.append(config)

    monkeypatch.setattr("openprogram.setup.update_config", capture_update)

    accepted = set_setting("mcp_server.exposed_tools", '["read", "memory_status"]')
    assert accepted == {
        "applied": "next_start",
        "value": ["read", "memory_status"],
    }
    assert saved == [{"mcp_server": {"exposed_tools": ["read", "memory_status"]}}]

    for invalid in ('{"read": true}', '["read", 7]', "null"):
        assert "error" in set_setting("mcp_server.exposed_tools", invalid)
    assert len(saved) == 1
