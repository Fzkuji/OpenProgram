from __future__ import annotations


def test_memory_settings_are_declared_and_validated(monkeypatch):
    from openprogram.config_schema import get_settings, set_setting

    monkeypatch.setattr("openprogram.setup._read_config", lambda: {})
    rows = {row["key"]: row for row in get_settings()}

    assert rows["memory.writer.enabled"]["value"] is True
    assert rows["memory.writer.trigger_tokens"]["value"] == 16_000
    assert rows["memory.retrieval.method"]["choices"] == [
        "bm25", "embedding", "hybrid",
    ]
    assert rows["memory.retrieval.top_k"]["value"] == 5
    assert rows["memory.retrieval.include_sources"]["value"] is True
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
    from openprogram.memory.management.config import load_memory_config

    config = load_memory_config({
        "memory": {
            "writer": {"enabled": False, "trigger_tokens": 8_000},
            "retrieval": {
                "method": "hybrid",
                "top_k": 8,
                "include_sources": False,
            },
            "core": {"inject": False},
            "recent": {"limit": 25},
        },
    })

    assert config.writer_enabled is False
    assert config.writer_trigger_tokens == 8_000
    assert config.retrieval_method == "hybrid"
    assert config.retrieval_top_k == 8
    assert config.retrieval_include_sources is False
    assert config.core_inject is False
    assert config.recent_limit == 25


def test_runtime_memory_config_rejects_fractional_and_boolean_integers():
    from openprogram.memory.management.config import load_memory_config

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
    from openprogram.memory.management import api

    (tmp_path / "topics").mkdir()
    (tmp_path / "topics/note.md").write_text("# Note\n", encoding="utf-8")
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"memory": {"recent": {"limit": 25}}},
    )
    seen = {}

    def fake_run(_root, **kwargs):
        seen["config"] = kwargs["config"]
        return []

    monkeypatch.setattr(api, "_run_agent", fake_run)

    assert api.organize_topics(tmp_path, agent=object()) == []
    assert seen["config"].recent_limit == 25


def test_embedding_status_requires_the_model_to_load_locally(monkeypatch):
    from openprogram.memory.retrieval import embedding, inspect

    monkeypatch.setattr(
        embedding, "load_default_encoder",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("model not cached")),
    )

    assert inspect.embedding_is_available() is False


def test_embedding_search_never_requests_network_model_loading(
    tmp_path, monkeypatch,
):
    import numpy as np
    import sentence_transformers

    from openprogram.memory.retrieval import embedding, inspect
    from openprogram.memory.retrieval.bm25 import MemoryEvent

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
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeEncoder)
    monkeypatch.setattr(embedding, "_default_encoder", None)
    monkeypatch.setattr(embedding.MemoryEmbeddingIndex, "_events", lambda _self: [event])

    result = inspect.search(tmp_path, "local model", method="embedding")

    assert result["results"][0]["event_id"] == "ev_one"
    assert seen == [{"local_files_only": True}]


def test_local_backend_uses_live_memory_settings(tmp_path, monkeypatch):
    from openprogram.memory.local_backend import LocalMemoryBackend
    from openprogram.memory.management.config import MemoryConfig

    configured = MemoryConfig(
        writer_enabled=False,
        retrieval_method="embedding",
        retrieval_top_k=3,
        retrieval_include_sources=False,
        core_inject=False,
    )
    monkeypatch.setattr(
        "openprogram.memory.local_backend.load_memory_config",
        lambda: configured,
    )
    monkeypatch.setattr("openprogram.memory.store.ensure", lambda: tmp_path)

    called = {}

    def fake_search(_root, query, **kwargs):
        called.update(query=query, **kwargs)
        return {"method": kwargs["method"], "results": []}

    monkeypatch.setattr(
        "openprogram.memory.retrieval.inspect.search", fake_search,
    )
    monkeypatch.setattr(
        "openprogram.memory.writing.write",
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
        "include_sources": False,
    }
    assert backend.write(session_id="s1", force=True) is None


def test_hybrid_search_fuses_results_and_can_exclude_sources(
    tmp_path, monkeypatch,
):
    from openprogram.memory.retrieval import inspect

    topic = tmp_path / "topics" / "one.md"
    source = tmp_path / "sources" / "one.md"
    topic.parent.mkdir()
    source.parent.mkdir()
    topic.write_text("# One\n", encoding="utf-8")
    source.write_text("# One\n", encoding="utf-8")
    seen = {}

    class FakeBM25:
        def __init__(self, _root, *, persist):
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

    monkeypatch.setattr("openprogram.memory.retrieval.bm25.MemoryBM25Index", FakeBM25)
    monkeypatch.setattr(
        "openprogram.memory.retrieval.embedding.MemoryEmbeddingIndex",
        FakeEmbedding,
    )

    result = inspect.search(
        tmp_path, "query", method="hybrid", top_k=3,
        include_sources=False,
    )

    assert result["method"] == "hybrid"
    assert [row["event_id"] for row in result["results"]] == [
        "shared", "lexical", "semantic",
    ]
    assert seen["bm25_prefix"] == "topics"
    assert seen["embedding_prefix"] == "topics"


def test_source_scope_keeps_topic_trust_while_hiding_source_hits(tmp_path):
    from contextlib import closing

    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.retrieval import inspect
    from openprogram.memory.runtime.state import SourceRecord
    from openprogram.memory.source_format import provider_source_location

    (tmp_path / "topics").mkdir()
    record = SourceRecord(
        provider="openprogram", thread_id="s1", message_id="m1", ordinal=0,
        role="user", content="source-only-evidence", trust_state="trusted",
        speaker_kind="owner", principal_id="owner/local",
        authority_tier="owner",
    )
    with closing(MemoryWorkspace(tmp_path)) as workspace:
        workspace.archive_source_records([record])
    location = provider_source_location(record.source_id, v2=True)
    assert location is not None
    source_path, anchor = location
    relative_source = source_path.relative_to("sources").as_posix()
    (tmp_path / "topics/note.md").write_text(
        "# Note\n\nCurated retained fact.[^e1] ^abc12345\n\n"
        "[^e1]: Time: `2026-01-01`; "
        f"Sources: [{record.source_id}]"
        f"(../sources/{relative_source}#{anchor})\n",
        encoding="utf-8",
    )

    result = inspect.search(
        tmp_path, "retained", method="bm25", include_sources=False,
    )

    assert len(result["results"]) == 1
    assert result["results"][0]["path"] == "topics/note.md"
    assert result["results"][0]["trust_state"] == "trusted"
    assert inspect.search(
        tmp_path, "source-only-evidence", method="bm25",
        include_sources=False,
    )["results"] == []
