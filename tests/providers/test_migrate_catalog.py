from openprogram.providers._migrate_catalog import migrate_catalog_file


def test_single_wire_extracts_default_endpoint():
    cat = {"deepseek/deepseek-chat": {
        "id": "deepseek-chat", "name": "X", "api": "openai-completions",
        "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "context_window": 128000, "cost": {"input": 0.27, "output": 1.1}}}
    pj, models = migrate_catalog_file(cat)
    assert pj["id"] == "deepseek"
    assert pj["endpoints"]["default"] == {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}
    assert len(models) == 1
    m = models[0]
    assert "api" not in m and "base_url" not in m and "provider" not in m
    assert m.get("endpoint", "default") == "default"
    assert m["cost"] == {"input": 0.27, "output": 1.1}  # nested preserved


def test_multi_wire_groups_endpoints():
    cat = {
        "opencode/a": {"id": "a", "name": "A", "api": "openai-completions", "provider": "opencode", "base_url": "https://opencode.ai/zen/v1"},
        "opencode/b": {"id": "b", "name": "B", "api": "anthropic-messages", "provider": "opencode", "base_url": "https://opencode.ai/zen"},
    }
    pj, models = migrate_catalog_file(cat)
    eps = pj["endpoints"]
    # two distinct (api, base_url) groups
    pairs = {(e["api"], e["base_url"]) for e in eps.values()}
    assert ("openai-completions", "https://opencode.ai/zen/v1") in pairs
    assert ("anthropic-messages", "https://opencode.ai/zen") in pairs
    by_id = {m["id"]: m for m in models}
    assert eps[by_id["a"].get("endpoint", "default")]["api"] == "openai-completions"
    assert eps[by_id["b"]["endpoint"]]["api"] == "anthropic-messages"


def test_key_prefix_mismatch_kept_as_separate_rows():
    # gemini double-key: same id, DIFFERENT name → keep BOTH rows, mark the
    # mismatched-prefix one with key_prefix. NO dedup (dedup would drop a name).
    cat = {
        "gemini-subscription/g": {"id": "g", "name": "G (Sub)", "api": "gemini-subscription", "provider": "gemini-subscription", "base_url": "https://x"},
        "google-gemini-cli/g": {"id": "g", "name": "G (CLI)", "api": "gemini-subscription", "provider": "gemini-subscription", "base_url": "https://x"},
    }
    pj, models = migrate_catalog_file(cat)
    assert pj["id"] == "gemini-subscription"
    g = [m for m in models if m["id"] == "g"]
    assert len(g) == 2  # both rows kept
    by_name = {m["name"]: m for m in g}
    assert "key_prefix" not in by_name["G (Sub)"]           # prefix == provider → default
    assert by_name["G (CLI)"]["key_prefix"] == "google-gemini-cli"
