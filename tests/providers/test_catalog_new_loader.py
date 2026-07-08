import json
from pathlib import Path
from openprogram.providers._catalog_new import load_provider_dir, load_new_catalog
from openprogram.providers.types import Model


def _write(root: Path, pid: str, provider_json: dict, models: list[dict]):
    d = root / pid
    d.mkdir(parents=True)
    (d / "provider.json").write_text(json.dumps(provider_json))
    (d / "catalog.json").write_text(json.dumps({"models": models}))


def test_single_wire_provider(tmp_path):
    _write(tmp_path, "deepseek",
           {"id": "deepseek", "endpoints": {"default": {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}}},
           [{"id": "deepseek-chat", "name": "DeepSeek Chat", "context_window": 128000,
             "cost": {"input": 0.27, "output": 1.1}}])
    got = load_provider_dir(tmp_path / "deepseek")
    m = got["deepseek/deepseek-chat"]
    assert isinstance(m, Model)
    assert m.api == "openai-completions"
    assert m.base_url == "https://api.deepseek.com/v1"
    assert m.provider == "deepseek"
    assert m.cost.input == 0.27 and m.cost.output == 1.1


def test_multi_wire_endpoint_resolution(tmp_path):
    _write(tmp_path, "opencode",
           {"id": "opencode", "endpoints": {
               "default": {"api": "openai-completions", "base_url": "https://opencode.ai/zen/v1"},
               "anthropic": {"api": "anthropic-messages", "base_url": "https://opencode.ai/zen"}}},
           [{"id": "gpt-x", "name": "GPT X"},  # default endpoint
            {"id": "claude-x", "name": "Claude X", "endpoint": "anthropic"}])
    got = load_provider_dir(tmp_path / "opencode")
    assert got["opencode/gpt-x"].api == "openai-completions"
    assert got["opencode/gpt-x"].base_url == "https://opencode.ai/zen/v1"
    assert got["opencode/claude-x"].api == "anthropic-messages"
    assert got["opencode/claude-x"].base_url == "https://opencode.ai/zen"


def test_key_prefix_produces_independent_models(tmp_path):
    # gemini double-key: same id, DIFFERENT name, different key prefix.
    _write(tmp_path, "gemini-subscription",
           {"id": "gemini-subscription", "endpoints": {"default": {"api": "gemini-subscription", "base_url": "https://cloudcode-pa.googleapis.com"}}},
           [{"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Subscription)"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Cloud Code Assist)", "key_prefix": "google-gemini-cli"}])
    got = load_provider_dir(tmp_path / "gemini-subscription")
    assert "gemini-subscription/gemini-2.5-pro" in got
    assert "google-gemini-cli/gemini-2.5-pro" in got
    # independent Models: each keeps its own name
    assert got["gemini-subscription/gemini-2.5-pro"].name == "Gemini 2.5 Pro (Subscription)"
    assert got["google-gemini-cli/gemini-2.5-pro"].name == "Gemini 2.5 Pro (Cloud Code Assist)"
    # both carry provider == provider.json.id, not the key prefix
    assert got["google-gemini-cli/gemini-2.5-pro"].provider == "gemini-subscription"


def test_missing_provider_json_yields_empty(tmp_path):
    (tmp_path / "wireonly").mkdir()
    assert load_provider_dir(tmp_path / "wireonly") == {}


def test_headers_and_input_preserved(tmp_path):
    _write(tmp_path, "github-copilot",
           {"id": "github-copilot", "endpoints": {"default": {"api": "openai-responses", "base_url": "https://api.individual.githubcopilot.com"}}},
           [{"id": "gpt-5.2-codex", "name": "GPT-5.2-Codex", "input": ["text", "image"],
             "headers": {"Copilot-Integration-Id": "vscode-chat"}}])
    m = load_provider_dir(tmp_path / "github-copilot")["github-copilot/gpt-5.2-codex"]
    assert m.input == ["text", "image"]
    assert m.headers == {"Copilot-Integration-Id": "vscode-chat"}
