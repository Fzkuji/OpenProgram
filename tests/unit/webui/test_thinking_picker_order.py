from openprogram.webui import _thinking


def test_picker_orders_descending_provider_levels_for_faster_to_smarter(monkeypatch):
    monkeypatch.setattr(
        "openprogram.providers.storage._read_providers_cfg",
        lambda: {"xai-subscription": {"models": [{
            "id": "grok-4.6",
            "thinking_levels": ["xhigh", "high", "medium", "low"],
            "default_thinking_level": "high",
        }]}},
    )

    config = _thinking.get_thinking_config_for_model(
        "xai-subscription", "grok-4.6",
    )

    assert [option["value"] for option in config["options"]] == [
        "off", "low", "medium", "high", "xhigh",
    ]
    assert config["default"] == "high"


def test_picker_deduplicates_and_orders_all_known_levels(monkeypatch):
    monkeypatch.setattr(
        "openprogram.providers.storage._read_providers_cfg",
        lambda: {"openai-codex": {"models": [{
            "id": "future",
            "thinking_levels": ["max", "low", "minimal", "xhigh", "low"],
        }]}},
    )

    config = _thinking.get_thinking_config_for_model("openai-codex", "future")

    assert [option["value"] for option in config["options"]] == [
        "off", "minimal", "low", "xhigh", "max",
    ]
