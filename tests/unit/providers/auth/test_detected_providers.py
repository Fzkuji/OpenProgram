"""Add provider offers keys found in the environment, local servers and the popular providers."""
from __future__ import annotations

import pytest

from openprogram.models.listing import detected
from openprogram.models.providers.catalog.consoles import POPULAR, console_url

_FAKE = "sk-test-0000000000000000abcd"


@pytest.fixture(autouse=True)
def _no_catalogue_refresh(monkeypatch):
    """The provider names come from the cached catalogue; a stale cache would
    start the models.dev refresh thread, which a unit test must not leave running."""
    from openprogram.models.providers.catalog.sources import models_dev
    monkeypatch.setattr(models_dev, "_refresh_held", True)
    monkeypatch.setattr(models_dev, "_refresh_wanted", False)


def test_a_key_in_the_environment_is_offered_by_name_with_a_masked_tail(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", _FAKE)
    monkeypatch.setenv("MOONSHOT_API_KEY", "Bearer " + _FAKE)
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", _FAKE)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rows = detected.env_keys(["deepseek", "kimi-coding", "moonshotai", "anthropic", "github-copilot"],
                             stored={"deepseek"})
    by_var = {row["env_var"]: row for row in rows}
    assert set(by_var) == {"DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"}  # never an OAuth token
    assert by_var["DEEPSEEK_API_KEY"]["connected"] is True
    # A shared variable goes to the popular provider first.
    assert by_var["MOONSHOT_API_KEY"]["provider"] == "moonshotai"
    assert by_var["MOONSHOT_API_KEY"]["providers"] == ["moonshotai", "kimi-coding"]
    assert all(_FAKE not in str(row) for row in rows)


def test_popular_lists_the_common_providers_with_how_they_connect(monkeypatch):
    rows = detected.popular(stored=set(), env=[{"env_var": "XAI_API_KEY", "provider": "xai", "label": "xAI",
                                                "providers": ["xai"], "masked": "…", "connected": False}])
    ids = [row["id"] for row in rows]
    assert ids == list(POPULAR)
    assert {"openai-codex", "anthropic", "google", "xai", "deepseek", "openrouter", "moonshotai", "zai"} <= set(ids)
    by_id = {row["id"]: row for row in rows}
    assert by_id["openai-codex"]["auth"] == "sign_in"
    # A tile offers the other ways its company table marks as offered.
    assert [v["id"] for v in by_id["openai-codex"]["variants"]] == ["openai"]
    assert [v["id"] for v in by_id["moonshotai"]["variants"]] == ["kimi-coding", "moonshotai-cn"]
    assert [v["id"] for v in by_id["zai"]["variants"]] == ["zai-coding-plan", "zhipuai"]
    # Anthropic only through its API key.
    assert by_id["anthropic"]["auth"] == "api_key" and by_id["anthropic"]["variants"] == []
    assert by_id["anthropic"]["console_url"] == console_url("anthropic") == "https://console.anthropic.com/settings/keys"
    assert by_id["xai"]["env_var"] == "XAI_API_KEY"



def test_an_address_off_this_machine_is_not_probed():
    assert detected.local_server_running("ollama", "http://gpu-box.lan:11434/v1") is None
