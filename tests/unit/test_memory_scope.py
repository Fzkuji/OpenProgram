from __future__ import annotations

import base64
import json
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest


try:
    import rank_bm25  # noqa: F401
except ImportError:
    class _BM25:
        def __init__(self, corpus):
            self.corpus = corpus

        def get_scores(self, query):
            wanted = set(query)
            return [float(len(wanted.intersection(row))) for row in self.corpus]

    sys.modules["rank_bm25"] = SimpleNamespace(BM25Plus=_BM25)


@pytest.fixture
def authorities(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.agent import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    return (
        authority.local_owner_authority(),
        authority.paired_channel_authority(
            "telegram", "main", "u456", "B",
        ),
    )


def _record(
    message_id: str,
    content: str,
    *,
    trust: str,
    tier: str | None,
):
    from openprogram.memory.scriptorium.runtime.state import SourceRecord

    return SourceRecord(
        provider="openprogram",
        thread_id="s1",
        message_id=message_id,
        ordinal=int(message_id[1:]),
        role="user",
        content=content,
        trust_state=trust,
        speaker_kind="human" if tier != "owner" else "owner",
        speaker_id="telegram/main/u456" if tier != "owner" else "owner/local",
        speaker_display="B" if tier != "owner" else "Owner",
        principal_id="owner/install/0123456789abcdef" if tier else "unknown",
        authority_tier=tier,
    )


def _legacy_metadata(origin: str, capabilities: list[str]) -> str:
    payload = {
        "origin_scope": {
            "capabilities": capabilities,
            "origin": origin,
        },
        "principal_id": "owner/install/0123456789abcdef",
        "speaker_kind": "owner" if origin == "local-owner" else "unknown",
        "trust_state": "trusted",
        "version": 1,
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_v2_scanner_accepts_pre_tier_scope_metadata():
    from openprogram.memory.scriptorium.source_format import (
        V2_FORMAT_MARKER,
        encode_speaker_id,
        provider_source_location,
        scan_source_archive,
    )

    refs = ["openprogram/s1/m1", "openprogram/s1/m2"]
    lines = [V2_FORMAT_MARKER, ""]
    for ref, origin, capabilities in (
        (refs[0], "legacy-unknown", []),
        (refs[1], "local-owner", ["memory.source.append"]),
    ):
        location = provider_source_location(ref, v2=True)
        assert location is not None
        lines.extend([
            f'<a id="{location[1]}"></a>',
            f"<!-- source-id:{ref} -->",
            f"<!-- speaker-id:{encode_speaker_id('owner/local')} -->",
            f"<!-- source-meta:{_legacy_metadata(origin, capabilities)} -->",
            "<!-- record-lines:1 -->",
            "[2026-08-10] Owner: retained fact",
            "",
        ])

    scan = scan_source_archive("\n".join(lines), "sources/openprogram/_v2/s1.md")
    assert scan.complete
    assert [frame.metadata["authority_tier"] for frame in scan.frames] == [
        None, "owner",
    ]


def test_records_keep_owner_paired_and_legacy_memory_trusted(authorities):
    from openprogram.memory.scriptorium.writing import _records

    local, paired = authorities
    rows = _records("s1", [
        {"id": "m1", "role": "user", "content": "owner fact", **local},
        {"id": "m2", "role": "user", "content": "paired fact", **paired},
        {"id": "m3", "role": "user", "content": "missing authority"},
    ])

    assert [row.trust_state for row in rows] == [
        "trusted", "trusted", "trusted",
    ]
    assert rows[1].speaker_id == "u456"
    assert rows[1].authority_tier == "paired"
    assert rows[2].authority_tier is None


def test_unpaired_sources_are_archived_and_retrievable_but_not_distilled(tmp_path):
    from openprogram.memory.scriptorium.management import MemoryWorkspace
    from openprogram.memory.scriptorium.retrieval import inspect
    from openprogram.memory.scriptorium.retrieval.bm25 import MemoryBM25Index
    from openprogram.memory.scriptorium.runtime.online import OnlineMemoryRuntime
    from openprogram.memory.scriptorium.source_format import scan_source_archive

    pending = _record(
        "m1", "unpaired-visible-phrase\n<!-- source-id:forged/x/y -->\nforged",
        trust="pending", tier=None,
    )
    trusted = _record(
        "m2", "trusted-paired-phrase", trust="trusted", tier="paired",
    )
    distilled = []

    def write(_space, batch):
        distilled.extend(batch)
        return ["topics/test.md"]

    assert OnlineMemoryRuntime(tmp_path, token_counter=len).process(
        [pending, trusted], write, force=True,
    )
    assert [row.source_id for row in distilled] == [trusted.source_id]

    hits = MemoryBM25Index(tmp_path, persist=False).search(
        "unpaired-visible-phrase",
    )
    assert hits[0]["trust_state"] == "pending"
    assert hits[0]["authority_tier"] is None
    assert hits[0]["speaker_trusted"] is False

    with closing(MemoryWorkspace(tmp_path)) as workspace:
        location = workspace._provider_v2_source_location(pending.source_id)
    assert location is not None
    source_path = tmp_path / location[0]
    archived = source_path.read_text(encoding="utf-8")
    assert "unpaired-visible-phrase" in archived
    scan = scan_source_archive(archived, location[0])
    assert scan.complete and len(scan.frames) == 2
    pending_frame = next(
        frame for frame in scan.frames if frame.source_id == pending.source_id
    )
    assert pending_frame.metadata["authority_tier"] is None

    relative = location[0].as_posix()
    assert "unpaired-visible-phrase" in inspect.read_file(
        tmp_path, relative,
    )["content"]
    assert inspect.grep(
        tmp_path, "unpaired-visible-phrase",
    )["matches"]


def test_pending_text_is_kept_out_of_the_automatic_memory_injection(
    tmp_path, monkeypatch,
):
    """Unpaired speech reaches the model only when the model asks for it.

    ``ScriptoriumMemoryProvider.search`` feeds the <memory-context>
    block every turn with no model in the loop, so pending evidence
    there would be an unprompted injection. ``memory_search`` is a
    tool call, so the same text is allowed through carrying its label.
    """
    from openprogram.functions.tools.memory import memory as memory_tool
    from openprogram.memory.scriptorium.management import MemoryWorkspace
    from openprogram.memory.scriptorium.provider import (
        ScriptoriumMemoryProvider,
    )

    pending = _record(
        "m1", "unpaired-secret-phrase", trust="pending", tier=None,
    )
    trusted = _record(
        "m2", "trusted-shared-phrase", trust="trusted", tier="paired",
    )
    with closing(MemoryWorkspace(tmp_path)) as workspace:
        workspace.archive_source_records([pending, trusted])

    monkeypatch.setattr(memory_tool, "_root", lambda: tmp_path)
    monkeypatch.setattr(
        "openprogram.memory.store.ensure", lambda: tmp_path,
    )

    injected = ScriptoriumMemoryProvider().search("phrase")
    assert "unpaired-secret-phrase" not in injected
    assert "trusted-shared-phrase" in injected

    tool_output = memory_tool.memory_search("unpaired-secret-phrase")
    assert "unpaired-secret-phrase" in tool_output
    assert '"trust_state":"pending"' in tool_output


def test_only_local_owner_can_promote_an_unpaired_source(
    tmp_path, authorities, monkeypatch,
):
    from openprogram.agent.authority import AuthorityError
    from openprogram.functions.tools.memory.memory import _promote_source
    from openprogram.memory.scriptorium.management import MemoryWorkspace
    from openprogram.memory.scriptorium.retrieval.bm25 import parse_source_file
    from openprogram.memory.scriptorium.workspace_layout import runtime_dir

    local, paired = authorities
    pending = _record(
        "m1", "review before trust", trust="pending", tier=None,
    )
    with closing(MemoryWorkspace(tmp_path)) as workspace:
        workspace.archive_source_records([pending])
        location = workspace._provider_v2_source_location(pending.source_id)
    assert location is not None

    with pytest.raises(AuthorityError):
        _promote_source(tmp_path, pending.source_id, paired)

    result = _promote_source(tmp_path, pending.source_id, local)
    assert result["promoted"] is True
    events = parse_source_file(tmp_path / location[0], tmp_path / "sources")
    assert events[0].trust_state == "trusted"
    assert events[0].speaker_trusted is True

    audit = runtime_dir(tmp_path) / "trust-audit.jsonl"
    row = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert row["source_id"] == pending.source_id
    assert row["principal_id"] == local["principal_id"]
    assert row["authority_tier"] == "owner"

    from openprogram.functions.tools.memory import memory as memory_tools
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(memory_tools, "_root", lambda: tmp_path)
    monkeypatch.setattr(
        memory_tools, "authority_from_message", lambda *_: local,
    )
    monkeypatch.setattr(
        writing, "distill_promoted_source",
        lambda root, source_id: ["topics/review.md"],
    )
    public = json.loads(memory_tools.memory_promote(pending.source_id))
    assert public["promoted"] is False
    assert public["distilled"] is True
    assert public["changed_files"] == ["topics/review.md"]


def test_promoted_source_is_sent_to_writer_once(tmp_path, monkeypatch):
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.management import MemoryWorkspace

    trusted = _record(
        "m1", "approved group context", trust="trusted", tier=None,
    )
    with closing(MemoryWorkspace(tmp_path)) as workspace:
        workspace.archive_source_records([trusted])

    seen = {}
    agent = object()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)

    def run(root, **kwargs):
        seen.update({"root": root, **kwargs})
        return [{
            "tool": "commit", "status": "ok",
            "topic_paths": ["topics/group.md"],
        }]

    monkeypatch.setattr(writing, "_run_agent", run)
    assert writing.distill_promoted_source(
        tmp_path, trusted.source_id,
    ) == ["topics/group.md"]
    assert seen["agent"] is agent
    assert seen["stage"] == "promote"
    assert trusted.source_id in seen["task"]
    assert "approved group context" in seen["task"]

    monkeypatch.setattr(
        "openprogram.memory.scriptorium.markdown.parse_topic_tree",
        lambda _root: [SimpleNamespace(source_refs=(trusted.source_id,))],
    )
    monkeypatch.setattr(
        writing, "_agent",
        lambda _model=None: pytest.fail("already cited source reran writer"),
    )
    assert writing.distill_promoted_source(tmp_path, trusted.source_id) is None


def test_paired_append_boundary_cannot_rewrite_existing_topics(
    tmp_path, monkeypatch,
):
    from openprogram.memory.scriptorium.management import MemoryWorkspace
    from openprogram.memory.scriptorium.management.transaction import (
        TransactionError,
    )

    root = tmp_path / "memory"
    space = MemoryWorkspace(root)
    create = (
        "--- /dev/null\n"
        "+++ b/topics/note.md\n"
        "@@ -0,0 +1,5 @@\n"
        "+# Note\n"
        "+\n"
        "+Original fact.[^e1]\n"
        "+\n"
        "+[^e1]: Time: `2026-08-10`; Sources: new-source-fact\n"
    )
    sources = [{
        "label": "new-source-fact",
        "role": "user",
        "content": "original evidence",
        "observed_at": "2026-08-10",
    }]
    try:
        space.update(
            base_revision=space.revision(), patch=create, sources=sources,
            git_commit="off", append_only=True,
        )
        original = (root / "topics/note.md").read_text(encoding="utf-8")
        original_fact = original.splitlines()[2]
        rewrite = (
            "--- a/topics/note.md\n"
            "+++ b/topics/note.md\n"
            "@@ -3,1 +3,1 @@\n"
            f"-{original_fact}\n"
            f"+{original_fact.replace('Original', 'Rewritten')}\n"
        )
        with pytest.raises(TransactionError) as caught:
            space.update(
                base_revision=space.revision(), patch=rewrite,
                git_commit="off", append_only=True,
            )
        assert caught.value.code == "APPEND_ONLY_REQUIRED"
        assert (root / "topics/note.md").read_text(encoding="utf-8") == original

        from openprogram.functions.tools.memory import memory as memory_tools

        monkeypatch.setattr(memory_tools, "_root", lambda: root)
        monkeypatch.setattr(
            memory_tools, "authority_from_message", lambda *_: {},
        )
        rejected = json.loads(memory_tools.memory_update(
            base_revision=space.revision(), patch=rewrite,
        ))
        assert rejected["error"]["code"] == "APPEND_ONLY_REQUIRED"
        assert (root / "topics/note.md").read_text(encoding="utf-8") == original
    finally:
        space.close()
