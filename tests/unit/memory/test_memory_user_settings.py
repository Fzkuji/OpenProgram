from __future__ import annotations

import pytest


def test_memory_settings_are_declared_and_validated(monkeypatch):
    from openprogram.state.settings import get_settings, set_setting

    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    rows = {row["key"]: row for row in get_settings()}

    assert rows["memory.writer.enabled"]["value"] is True
    assert rows["memory.writer.trigger_tokens"]["value"] == 16_000
    assert rows["memory.retrieval.method"]["choices"] == [
        "agent", "bm25", "embedding", "hybrid",
    ]
    assert rows["memory.retrieval.top_k"]["value"] == 5
    assert "memory.retrieval.include_sources" not in rows
    assert rows["memory.core.inject"]["value"] is True
    assert rows["memory.recent.limit"]["value"] == 50

    assert "error" in set_setting("memory.writer.trigger_tokens", 12_345)
    assert "error" in set_setting("memory.retrieval.top_k", 11)
    assert "error" in set_setting("memory.recent.limit", 0)
    for invalid in (True, 8.9, "8.9"):
        assert "error" in set_setting("memory.retrieval.top_k", invalid)
    for invalid in (True, 25.5, "25.5"):
        assert "error" in set_setting("memory.recent.limit", invalid)
    assert "error" in set_setting("memory.writer.trigger_tokens", 8_000.9)


def test_runtime_memory_config_reads_nested_settings():
    from openprogram.state.memory.management.config import load_memory_config

    config = load_memory_config({
        "memory": {
            "writer": {"enabled": False, "trigger_tokens": 8_000},
            "retrieval": {
                "method": "hybrid",
                "top_k": 8,
            },
            "core": {"inject": False},
            "recent": {"limit": 25},
        },
    })

    assert config.writer_enabled is False
    assert config.writer_trigger_tokens == 8_000
    assert config.retrieval_method == "hybrid"
    assert config.retrieval_top_k == 8
    assert config.core_inject is False
    assert config.recent_limit == 25


def test_agent_recall_mode_skips_ranked_automatic_and_tool_search(
    tmp_path, monkeypatch,
):
    from openprogram.state.memory.local_backend import LocalMemoryBackend
    from openprogram.state.memory.management.config import MemoryConfig
    from openprogram.programs.tools.knowledge.memory import (
        memory as memory_tool,
    )

    config = MemoryConfig(retrieval_method="agent")
    monkeypatch.setattr(
        "openprogram.state.memory.local_backend.load_memory_config", lambda: config,
    )
    monkeypatch.setattr(memory_tool, "load_memory_config", lambda: config)
    monkeypatch.setattr(
        "openprogram.state.memory.retrieval.inspect.search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("agent mode must not run ranked retrieval")
        ),
    )

    assert LocalMemoryBackend().search("remembered") == ""
    # The literal match still runs; only ranking is off.
    monkeypatch.setattr("openprogram.state.memory.store.ensure", lambda: tmp_path)
    assert memory_tool.memory_search("remembered") == (
        "No line in memory contains 'remembered'."
    )


def test_runtime_memory_config_rejects_fractional_and_boolean_integers():
    from openprogram.state.memory.management.config import load_memory_config

    config = load_memory_config({
        "memory": {
            "writer": {"trigger_tokens": 8_000.9},
            "retrieval": {"top_k": True},
            "recent": {"limit": "25.5"},
        },
    })

    assert config.writer_trigger_tokens == 16_000
    assert config.retrieval_top_k == 5
    assert config.recent_limit == 50


def test_organize_topics_uses_live_recent_limit(tmp_path, monkeypatch):
    from openprogram.state.memory.management import api

    (tmp_path / "topics").mkdir()
    # A never-recorded file is ours, and the organizer's to rewrite, only
    # when every paragraph cites; anything else came from outside.
    (tmp_path / "topics/note.md").write_text(
        "# Note\n\nA fact.[^e-1f4c7a2b90] ^abc12345\n\n"
        "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"memory": {"recent": {"limit": 25}}},
    )
    seen = {}

    def fake_run(_root, **kwargs):
        seen["config"] = kwargs["config"]
        return []

    monkeypatch.setattr(api, "_run_agent", fake_run)

    assert api.organize_topics(tmp_path, agent=object()) == []
    assert seen["config"].recent_limit == 25


def test_embedding_search_without_sentence_transformers_is_unavailable(
    tmp_path, monkeypatch,
):
    import sys

    from openprogram.state.memory.management.transaction import TransactionError
    from openprogram.state.memory.retrieval import embedding, inspect
    from openprogram.state.memory.retrieval.bm25 import MemoryEvent

    event = MemoryEvent(
        event_id="ev_one", path="topics/one.md", line=1,
        headings=["One"], date="2026-08-16", dates=["2026-08-16"],
        content="remember this", refs=[],
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setattr(embedding, "_default_encoder", None)
    monkeypatch.setattr(
        embedding.MemoryEmbeddingIndex, "_events", lambda _self: [event],
    )
    inspect._clear_search_index_cache_for_tests()

    with pytest.raises(TransactionError) as error:
        inspect.search(tmp_path, "remember", method="embedding")
    assert error.value.code == "EMBEDDING_UNAVAILABLE"
    assert "sentence-transformers is not installed" in error.value.message
    assert inspect.search(tmp_path, "remember", method="bm25")["method"] == "bm25"
    assert embedding.default_model_is_available() is False


def test_embedding_search_reports_intel_macos_limitation(tmp_path, monkeypatch):
    import platform
    import sys

    from openprogram.state.memory.management.transaction import TransactionError
    from openprogram.state.memory.retrieval import embedding, inspect
    from openprogram.state.memory.retrieval.bm25 import MemoryEvent

    event = MemoryEvent(
        event_id="ev_one", path="topics/one.md", line=1,
        headings=["One"], date="2026-08-16", dates=["2026-08-16"],
        content="remember this", refs=[],
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(embedding, "_default_encoder", None)
    monkeypatch.setattr(
        embedding.MemoryEmbeddingIndex, "_events", lambda _self: [event],
    )
    inspect._clear_search_index_cache_for_tests()

    with pytest.raises(TransactionError) as error:
        inspect.search(tmp_path, "remember", method="embedding")
    assert error.value.code == "EMBEDDING_UNAVAILABLE"
    assert "Intel macOS" in error.value.message
    assert "use BM25" in error.value.message
    assert inspect.search(tmp_path, "remember", method="bm25")["method"] == "bm25"


@pytest.mark.parametrize("cached", [True, False])
def test_intel_macos_embedding_status_ignores_model_cache(monkeypatch, cached):
    import platform
    import sys

    from openprogram.state.memory.retrieval import embedding_model, inspect

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        embedding_model, "default_model_is_cached", lambda: cached,
    )

    assert inspect.embedding_is_available() is False


def test_intel_macos_cache_probe_and_install_skip_network(monkeypatch):
    import platform
    import sys
    from types import SimpleNamespace

    from openprogram.state.memory.retrieval import embedding_model

    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            snapshot_download=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            ),
        ),
    )

    assert embedding_model.default_model_is_cached() is False
    with pytest.raises(RuntimeError, match="use BM25"):
        embedding_model.install_default_model()
    assert calls == []


def test_memory_embedding_setting_reports_intel_macos_limitation(monkeypatch):
    import platform
    import sys

    from openprogram.state.settings import set_setting

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        "openprogram.state.config.update_config",
        lambda _mutator: (_ for _ in ()).throw(
            AssertionError("an unavailable embedding method must not persist")
        ),
    )

    result = set_setting("memory.retrieval.method", "embedding")

    assert "Intel macOS" in result["error"]
    assert "use BM25" in result["error"]


def test_embedding_status_checks_local_snapshot_without_loading_encoder(monkeypatch):
    from openprogram.state.memory.retrieval import embedding, embedding_model, inspect

    loaded = []
    # The status check itself is under test, not this machine's packages.
    monkeypatch.setattr(embedding_model, "platform_unavailable_reason", lambda: None)
    monkeypatch.setattr(embedding_model, "packages_available", lambda: True)
    monkeypatch.setattr(
        embedding_model, "default_model_is_cached", lambda: True,
    )

    def fail_if_loaded(**kwargs):
        loaded.append(kwargs)
        raise AssertionError("status must not load the embedding encoder")

    monkeypatch.setattr(embedding, "load_default_encoder", fail_if_loaded)

    assert inspect.embedding_is_available() is True
    assert loaded == []


def test_default_model_cache_probe_stays_local(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from openprogram.state.memory.retrieval import embedding_model

    seen = []
    snapshot = tmp_path / "snapshot"
    for relative in embedding_model.MODEL_FILES:
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"present")

    def snapshot_download(model_id, *, allow_patterns, local_files_only):
        seen.append((model_id, allow_patterns, local_files_only))
        return str(snapshot)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    assert embedding_model.default_model_is_cached() is True
    assert seen == [(
        embedding_model.MODEL_ID, embedding_model.MODEL_FILES, True,
    )]

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            snapshot_download=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("model not cached")
            ),
        ),
    )
    assert embedding_model.default_model_is_cached() is False


def test_default_model_cache_probe_rejects_partial_snapshot(
    tmp_path, monkeypatch,
):
    import sys
    from types import SimpleNamespace

    from openprogram.state.memory.retrieval import embedding_model

    snapshot = tmp_path / "partial"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda *_args, **_kwargs: snapshot),
    )

    assert embedding_model.default_model_is_cached() is False


def test_install_default_model_downloads_only_encoder_files(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from openprogram.state.memory.retrieval import embedding_model

    seen = []
    snapshot = tmp_path / "snapshot"
    for relative in embedding_model.MODEL_FILES:
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"present")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            snapshot_download=lambda model_id, **kwargs: (
                seen.append((model_id, kwargs)) or snapshot
            )
        ),
    )

    embedding_model.install_default_model()

    assert seen == [(
        embedding_model.MODEL_ID,
        {"allow_patterns": embedding_model.MODEL_FILES},
    )]


def test_embedding_search_never_requests_network_model_loading(
    tmp_path, monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    from openprogram.state.memory.retrieval import embedding, inspect
    from openprogram.state.memory.retrieval.bm25 import MemoryEvent

    seen = []

    class FakeEncoder:
        def __init__(self, _model_id, **kwargs):
            seen.append(kwargs)

        def encode(self, values):
            return np.ones((len(values), 2), dtype=float)

    event = MemoryEvent(
        event_id="ev_one", path="topics/one.md", line=1,
        headings=["One"], date="2026-08-16", dates=["2026-08-16"],
        content="remember the local model", refs=[],
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeEncoder),
    )
    monkeypatch.setattr(embedding, "_default_encoder", None)
    monkeypatch.setattr(embedding.MemoryEmbeddingIndex, "_events", lambda _self: [event])

    result = inspect.search(tmp_path, "local model", method="embedding")

    assert result["results"][0]["event_id"] == "ev_one"
    assert seen == [{"local_files_only": True}]


def test_local_backend_uses_live_memory_settings(tmp_path, monkeypatch):
    from openprogram.state.memory.local_backend import LocalMemoryBackend
    from openprogram.state.memory.management.config import MemoryConfig

    configured = MemoryConfig(
        writer_enabled=False,
        retrieval_method="embedding",
        retrieval_top_k=3,
        core_inject=False,
    )
    monkeypatch.setattr(
        "openprogram.state.memory.local_backend.load_memory_config",
        lambda: configured,
    )
    monkeypatch.setattr("openprogram.state.memory.store.ensure", lambda: tmp_path)

    called = {}

    def fake_search(_root, query, **kwargs):
        called.update(query=query, **kwargs)
        return {"method": kwargs["method"], "results": []}

    monkeypatch.setattr(
        "openprogram.state.memory.retrieval.inspect.search", fake_search,
    )
    monkeypatch.setattr(
        "openprogram.state.memory.writing.write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled writer must not run")
        ),
    )

    backend = LocalMemoryBackend()
    assert backend.system_prompt() == ""
    assert backend.search("remembered") == ""
    assert called == {
        "query": "remembered",
        "method": "embedding",
        "top_k": 3,
    }
    assert backend.write(session_id="s1", force=True) is None


def test_hybrid_search_fuses_results(
    tmp_path, monkeypatch,
):
    from openprogram.state.memory.retrieval import inspect

    topic = tmp_path / "topics" / "one.md"
    topic.parent.mkdir()
    topic.write_text("# One\n", encoding="utf-8")
    seen = {}

    class FakeBM25:
        def __init__(self, _root):
            pass

        def search(self, _query, **kwargs):
            seen["bm25_prefix"] = kwargs["path_prefix"]
            return [
                {"event_id": "shared", "path": "topics/one.md", "line": 2,
                 "content": "shared"},
                {"event_id": "lexical", "path": "topics/one.md", "line": 3,
                 "content": "lexical"},
            ]

    class FakeEmbedding:
        def __init__(self, _root):
            pass

        def search(self, _query, **kwargs):
            seen["embedding_prefix"] = kwargs["path_prefix"]
            return [
                {"event": "shared", "path": "topics/one.md", "line": 2,
                 "content": "shared"},
                {"event": "semantic", "path": "topics/one.md", "line": 4,
                 "content": "semantic"},
            ]

    monkeypatch.setattr("openprogram.state.memory.retrieval.bm25.MemoryBM25Index", FakeBM25)
    monkeypatch.setattr(
        "openprogram.state.memory.retrieval.embedding.MemoryEmbeddingIndex",
        FakeEmbedding,
    )

    result = inspect.search(
        tmp_path, "query", method="hybrid", top_k=3,
    )

    assert result["method"] == "hybrid"
    assert [row["event_id"] for row in result["results"]] == [
        "shared", "lexical", "semantic",
    ]
    assert seen["bm25_prefix"] is None
    assert seen["embedding_prefix"] is None


def test_bm25_search_reuses_its_index_until_workspace_changes(
    tmp_path, monkeypatch,
):
    from openprogram.state.memory.retrieval import bm25, inspect

    topic = tmp_path / "topics/one.md"
    topic.parent.mkdir()
    topic.write_text("# One\n", encoding="utf-8")
    # A per-file cache an older build left beside the workspace goes away.
    stale = tmp_path / ".scriptorium-bm25.json"
    stale.write_text("{}", encoding="utf-8")
    built = []
    original = bm25.topic_events

    def counted(*args, **kwargs):
        built.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(bm25, "topic_events", counted)
    inspect._clear_search_index_cache_for_tests()

    inspect.search(tmp_path, "first")
    assert len(built) == 1
    inspect.search(tmp_path, "second")
    assert len(built) == 1
    topic.write_text("# One\n\nChanged.\n", encoding="utf-8")
    inspect.search(tmp_path, "third")

    assert len(built) == 2
    assert not stale.exists()


def test_embedding_search_reuses_document_vectors_until_workspace_changes(
    tmp_path, monkeypatch,
):
    import numpy as np

    from openprogram.state.memory.retrieval import embedding, inspect
    from openprogram.state.memory.retrieval.bm25 import MemoryEvent

    topic = tmp_path / "topics/one.md"
    topic.parent.mkdir()
    topic.write_text("# One\n", encoding="utf-8")
    encoded = []

    class FakeEncoder:
        def encode(self, values):
            encoded.append(list(values))
            return np.ones((len(values), 2), dtype=float)

    event = MemoryEvent(
        event_id="ev_one", path="topics/one.md", line=1,
        headings=["One"], date="2026-08-16", dates=["2026-08-16"],
        content="remember the cached document", refs=[],
    )
    monkeypatch.setattr(
        embedding.MemoryEmbeddingIndex, "_events", lambda _self: [event],
    )
    monkeypatch.setattr(
        embedding, "load_default_encoder", lambda **_kwargs: FakeEncoder(),
    )
    inspect._clear_search_index_cache_for_tests()

    inspect.search(tmp_path, "first", method="embedding")
    inspect.search(tmp_path, "second", method="embedding")
    topic.write_text("# One\n\nChanged.\n", encoding="utf-8")
    inspect.search(tmp_path, "third", method="embedding")

    assert [len(batch) for batch in encoded] == [1, 1, 1, 1, 1]
    assert "remember the cached document" in encoded[0][0]
    assert "remember the cached document" in encoded[3][0]
