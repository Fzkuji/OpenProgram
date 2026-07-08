from openprogram.providers._provider_meta import provider_apis, provider_base_url


def test_single_wire_meta():
    assert provider_apis("deepseek") == {"openai-completions"}
    assert provider_base_url("deepseek") == "https://api.deepseek.com/v1"


def test_multi_wire_meta():
    apis = provider_apis("opencode")
    assert "anthropic-messages" in apis and "openai-completions" in apis


def test_unknown_provider():
    assert provider_apis("nope") == set()
    assert provider_base_url("nope") is None
