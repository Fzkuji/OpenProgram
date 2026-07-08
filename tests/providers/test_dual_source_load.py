from openprogram.providers.models_generated import MODEL_REGISTRY, _load
from openprogram.providers.models import get_model, get_providers


def test_models_still_populated():
    assert len(MODEL_REGISTRY) > 700  # 752 keys pre-migration; new source reproduces them
    # spot-check across providers/wires
    assert get_model("openai", "gpt-4o") is not None
    assert get_model("opencode", "gpt-5.1-codex-max") is not None
    # gemini double-key both resolve
    assert get_model("gemini-subscription", "gemini-2.5-pro") is not None
    assert get_model("google-gemini-cli", "gemini-2.5-pro") is not None


def test_models_is_mutable_same_object():
    # _register_custom_model_in_registry writes MODEL_REGISTRY[k]=m in place
    before = id(MODEL_REGISTRY)
    MODEL_REGISTRY["__test__/x"] = MODEL_REGISTRY["openai/gpt-4o"]
    assert id(MODEL_REGISTRY) == before
    del MODEL_REGISTRY["__test__/x"]


def test_load_reads_new_source(monkeypatch):
    # _load must actually merge load_new_catalog. Wrap it to inject a sentinel
    # key _catalog can never contain, and assert _load surfaces it.
    from openprogram.providers import _catalog_new

    real = _catalog_new.load_new_catalog

    def wrapped(root):
        merged = dict(real(root))
        probe = next(iter(merged.values()), None)
        if probe is not None:
            merged["__sentinel__/probe"] = probe
        return merged

    monkeypatch.setattr(_catalog_new, "load_new_catalog", wrapped)
    assert "__sentinel__/probe" in _load()  # proves _load called the new-source loader
