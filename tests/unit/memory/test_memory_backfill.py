"""One-shot recovery of archived sources that never reached a Topic."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _without_agent_sdk(monkeypatch: pytest.MonkeyPatch):
    """The fake agent edits the real stage without SDK-decorated tool wrappers."""
    monkeypatch.setattr(
        "openprogram.memory.management.agent.management_tools",
        lambda _workspace, _audit: [],
    )
    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        SimpleNamespace(
            get_encoding=lambda _name: SimpleNamespace(encode=lambda text: list(text))
        ),
    )
    monkeypatch.setitem(sys.modules, "rank_bm25", SimpleNamespace(BM25Plus=object))
    yield
    sys.modules.pop("openprogram.memory.retrieval.bm25", None)


def _record(
    message_id: str,
    content: str,
    *,
    ordinal: int = 1,
    trust_state: str = "trusted",
):
    from openprogram.memory.runtime.state import SourceRecord

    return SourceRecord(
        provider="openprogram",
        thread_id="backfill",
        message_id=message_id,
        ordinal=ordinal,
        role="user",
        content=content,
        timestamp="2026-08-10T12:00:00+08:00",
        speaker_id="owner",
        speaker_display="Owner",
        speaker_kind="human",
        principal_id="owner/install/0123456789abcdef",
        authority_tier="owner" if trust_state == "trusted" else None,
        trust_state=trust_state,
    )


def _archive(root: Path, *records) -> None:
    from openprogram.memory.management import MemoryWorkspace

    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records(list(records))


def _legacy_source(root: Path, source_id: str, content: str) -> None:
    from openprogram.memory.source_format import provider_source_location

    relative, anchor = provider_source_location(source_id)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<a id="{anchor}"></a>\n'
        f"<!-- source-id:{source_id} -->\n"
        f"[2026-08-09] user: {content}\n",
        encoding="utf-8",
    )


def _source_link(root: Path, source_id: str) -> str:
    from openprogram.memory.source_format import provider_source_location

    v2 = provider_source_location(source_id, v2=True)
    legacy = provider_source_location(source_id)
    assert v2 is not None and legacy is not None
    relative, anchor = v2 if (root / v2[0]).is_file() else legacy
    return f"../{relative.as_posix()}#{anchor}"


def _append_topic(
    root: Path,
    source_ids: list[str],
    path: str = "core.md",
    contents: list[str] | None = None,
) -> None:
    target = root / "topics" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else "# Memory\n"
    additions = []
    for index, source_id in enumerate(source_ids):
        digest = hashlib.sha256(source_id.encode()).hexdigest()
        evidence_id = "e" + digest[:10]
        block_id = digest[10:18]
        fact = (
            " ".join(contents[index].split())
            if contents is not None
            else f"Backfilled {source_id}."
        )
        additions.append(
            f"{fact}[^{evidence_id}] ^{block_id}\n\n"
            f"[^{evidence_id}]: Time: `2026-08-10`; Sources: "
            f"[{source_id}]({source_id})"
        )
    target.write_text(
        existing.rstrip() + "\n\n" + "\n\n".join(additions) + "\n",
        encoding="utf-8",
    )


class _WriterAgent:
    """External-model boundary: write valid Topics for the supplied refs."""

    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        extra_refs: list[str] | None = None,
    ):
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call
        self.extra_refs = extra_refs or []

    def run(self, *, prompt: str, cwd: Path, **_kwargs):
        rows = [
            json.loads(line)
            for line in prompt.splitlines()
            if line.startswith('{"ref":')
        ]
        refs = [row["ref"] for row in rows]
        self.calls.append(refs)
        if self.fail_on_call == len(self.calls):
            partial = Path(cwd) / "topics" / "partial.md"
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text("# Uncommitted\n", encoding="utf-8")
            raise RuntimeError("writer unavailable")
        _append_topic(
            Path(cwd),
            refs + self.extra_refs,
            contents=[row["content"] for row in rows]
            + ["forbidden source"] * len(self.extra_refs),
        )
        return SimpleNamespace(
            turns=[],
            reply="",
            text="",
            num_turns=1,
            input_tokens=1,
            output_tokens=1,
            stop_reason="end_turn",
            anthropic_equivalent_cost_usd=0.0,
        )


class _SourceVisibilityAgent(_WriterAgent):
    """Attempt to substitute staged Source content for the selected batch."""

    def run(self, *, prompt: str, cwd: Path, **kwargs):
        exposed = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (Path(cwd) / "sources").rglob("*.md")
        )
        rows = [
            json.loads(line)
            for line in prompt.splitlines()
            if line.startswith('{"ref":')
        ]
        if "pending source must remain hidden" in exposed:
            rows[0]["content"] = "pending source must remain hidden"
        refs = [row["ref"] for row in rows]
        self.calls.append(refs)
        _append_topic(
            Path(cwd), refs, contents=[row["content"] for row in rows],
        )
        return SimpleNamespace(
            turns=[], reply="", text="", num_turns=1,
            input_tokens=1, output_tokens=1, stop_reason="end_turn",
            anthropic_equivalent_cost_usd=0.0,
        )


def _topic_refs(root: Path) -> set[str]:
    from openprogram.memory.markdown import parse_topic_tree

    return {
        ref
        for unit in parse_topic_tree(root / "topics")
        for ref in unit.source_refs
    }


def test_backfill_ignores_markers_and_sends_only_uncited_trusted_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

    root = tmp_path / "memory"
    cited = _record("cited", "already represented", ordinal=1)
    fresh = _record("fresh", "new trusted fact", ordinal=2)
    pending = _record(
        "pending", "unpaired group speech", ordinal=3, trust_state="pending",
    )
    _archive(root, cited, fresh, pending)
    legacy_id = "openprogram/legacy/old"
    _legacy_source(root, legacy_id, "trusted legacy fact")
    _append_topic(root, [cited.source_id], path="existing.md")
    (root / "core.md").write_text(
        "Legacy core without citations or block IDs.\n", encoding="utf-8",
    )
    agent = _WriterAgent()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    monkeypatch.setattr(writing, "_counter", lambda: len)
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: pytest.fail("backfill must not consult session written markers"),
    )

    report = writing.backfill(root, batch_token_budget=10_000)

    assert report["candidates"] == 3
    assert report["processed"] == 3
    assert report["remaining"] == 0
    assert len(report["revision"]) == 32
    assert len(agent.calls) == 1
    migration_refs = {
        ref for ref in agent.calls[0] if ref.startswith("openprogram-migration/")
    }
    assert set(agent.calls[0]) == {fresh.source_id, legacy_id, *migration_refs}
    assert len(migration_refs) == 1
    assert _topic_refs(root) == {
        cited.source_id, fresh.source_id, legacy_id, *migration_refs,
    }
    assert pending.source_id not in (root / "topics/core.md").read_text(
        encoding="utf-8"
    )
    for path in (root / "topics/core.md", root / "core.md"):
        assert "Legacy core without citations or block IDs" in path.read_text(
            encoding="utf-8"
        )


def test_backfill_is_idempotent_and_does_not_construct_an_agent_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing
    from openprogram.memory.management.transaction import (
        workspace_revision,
    )

    root = tmp_path / "memory"
    record = _record("once", "write once")
    _archive(root, record)
    agent = _WriterAgent()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    monkeypatch.setattr(writing, "_counter", lambda: len)
    first = writing.backfill(root, batch_token_budget=10_000)
    first_revision = workspace_revision(root)

    monkeypatch.setattr(
        writing,
        "_agent",
        lambda _model=None: pytest.fail("an already cited source was resent"),
    )
    second = writing.backfill(root, batch_token_budget=10_000)

    assert first["processed"] == 1
    assert second == {
        "status": "ok",
        "candidates": 0,
        "processed": 0,
        "remaining": 0,
        "revision": first_revision,
    }
    assert workspace_revision(root) == first_revision


def test_backfill_transaction_rejects_a_pending_ref_the_agent_reads_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing
    from openprogram.memory.management.transaction import (
        TransactionError,
    )

    root = tmp_path / "memory"
    trusted = _record("trusted", "allowed source", ordinal=1)
    pending = _record(
        "pending", "must not be distilled", ordinal=2, trust_state="pending",
    )
    _archive(root, trusted, pending)
    agent = _WriterAgent(extra_refs=[pending.source_id])
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    monkeypatch.setattr(writing, "_counter", lambda: len)

    # A pending Source is refused by the central trust rule before the
    # batch-locality rule ever sees it; either refusal keeps it out.
    with pytest.raises(TransactionError, match="not trusted|selected batch"):
        writing.backfill(root, batch_token_budget=10_000)

    assert agent.calls == [[trusted.source_id], [trusted.source_id]]
    assert list((root / "topics").rglob("*.md")) == []


def test_backfill_does_not_expose_unselected_sources_to_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

    root = tmp_path / "memory"
    trusted = _record("trusted-visible", "selected trusted source", ordinal=1)
    pending = _record(
        "pending-hidden",
        "pending source must remain hidden",
        ordinal=2,
        trust_state="pending",
    )
    _archive(root, trusted, pending)
    agent = _SourceVisibilityAgent()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    monkeypatch.setattr(writing, "_counter", lambda: len)

    report = writing.backfill(root, batch_token_budget=10_000)

    assert report["processed"] == 1
    topic = (root / "topics/core.md").read_text(encoding="utf-8")
    assert "selected trusted source" in topic
    assert "pending source must remain hidden" not in topic


def test_backfill_restricts_intermediate_workspace_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

    root = tmp_path / "memory"
    trusted = _record("trusted-intermediate", "allowed", ordinal=1)
    pending = _record(
        "pending-intermediate", "forbidden", ordinal=2, trust_state="pending",
    )
    _archive(root, trusted, pending)
    captured = {}

    def capture_workspace(workspace, _audit):
        captured["workspace"] = workspace
        return []

    class IntermediateCommitAgent(_WriterAgent):
        def run(self, *, prompt: str, cwd: Path, **_kwargs):
            rows = [
                json.loads(line)
                for line in prompt.splitlines()
                if line.startswith('{"ref":')
            ]
            refs = [row["ref"] for row in rows]
            self.calls.append(refs)
            workspace = captured["workspace"]
            baseline = workspace.baseline()
            _append_topic(
                Path(cwd),
                refs + [pending.source_id],
                contents=[row["content"] for row in rows] + ["forbidden source"],
            )
            workspace.commit_edits(*baseline)
            return SimpleNamespace(
                turns=[], reply="", text="", num_turns=1,
                input_tokens=1, output_tokens=1, stop_reason="end_turn",
                anthropic_equivalent_cost_usd=0.0,
            )

    agent = IntermediateCommitAgent()
    monkeypatch.setattr(
        "openprogram.memory.management.agent.management_tools",
        capture_workspace,
    )
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    monkeypatch.setattr(writing, "_counter", lambda: len)

    with pytest.raises(ValueError, match="not trusted|selected batch"):
        writing.backfill(root, batch_token_budget=10_000)

    assert agent.calls == [[trusted.source_id]]
    assert list((root / "topics").rglob("*.md")) == []


def test_restricted_backfill_transaction_can_replace_current_memory(
    tmp_path: Path,
):
    from openprogram.memory.management import MemoryWorkspace

    root = tmp_path / "memory"
    cited = _record("keep-cited", "existing", ordinal=1)
    fresh = _record("new-citation", "new", ordinal=2)
    _archive(root, cited, fresh)
    _append_topic(root, [cited.source_id], path="existing.md")

    with closing(MemoryWorkspace(
        root, allowed_new_source_refs={fresh.source_id},
    )) as workspace:
        baseline = workspace.baseline()
        topic = workspace.stage_dir / "topics/existing.md"
        topic.write_text(
            topic.read_text(encoding="utf-8")
            .replace(cited.source_id, fresh.source_id)
            .replace(
                _source_link(root, cited.source_id),
                _source_link(root, fresh.source_id),
            ),
            encoding="utf-8",
        )
        workspace.commit_edits(*baseline)

    assert _topic_refs(root) == {fresh.source_id}


def test_a_failed_batch_rolls_back_and_a_retry_resumes_at_its_first_uncited_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

    root = tmp_path / "memory"
    records = [
        _record(f"m{index}", character * 6, ordinal=index)
        for index, character in enumerate("abc", start=1)
    ]
    _archive(root, *records)
    failing = _WriterAgent(fail_on_call=2)
    monkeypatch.setattr(writing, "_agent", lambda _model=None: failing)
    monkeypatch.setattr(writing, "_counter", lambda: len)

    with pytest.raises(RuntimeError, match="writer unavailable"):
        writing.backfill(root, batch_token_budget=10)

    assert failing.calls == [[records[0].source_id], [records[1].source_id]]
    assert _topic_refs(root) == {records[0].source_id}
    assert not (root / "topics/partial.md").exists()
    assert records[1].content not in (root / "topics/core.md").read_text(
        encoding="utf-8"
    )

    resumed = _WriterAgent()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: resumed)
    report = writing.backfill(root, batch_token_budget=10)

    assert resumed.calls == [[records[1].source_id], [records[2].source_id]]
    assert report["candidates"] == 2
    assert report["processed"] == 2
    assert report["remaining"] == 0
    assert _topic_refs(root) == {record.source_id for record in records}


def test_first_batch_failure_preserves_the_only_legacy_core_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing
    from openprogram.memory.retrieval.bm25 import parse_source_file

    root = tmp_path / "memory"
    owner_principal = "owner/install/0123456789abcdef"
    monkeypatch.setattr(
        "openprogram.agent.authority.owner_principal_id",
        lambda: owner_principal,
    )
    record = _record("rollback", "must survive")
    _archive(root, record)
    source = next((root / "sources").rglob("*.md"))
    source_before = source.read_bytes()
    core_before = b"the only legacy core copy\n"
    (root / "core.md").write_bytes(core_before)
    monkeypatch.setattr(
        writing, "_agent", lambda _model=None: _WriterAgent(fail_on_call=1),
    )
    monkeypatch.setattr(writing, "_counter", lambda: len)

    with pytest.raises(RuntimeError, match="writer unavailable"):
        writing.backfill(root, batch_token_budget=10_000)

    assert source.read_bytes() == source_before
    assert (root / "core.md").read_bytes() == core_before
    assert list((root / "topics").rglob("*.md")) == []

    migration_sources = list(
        (root / "sources" / "openprogram-migration" / "_v2").glob("*.md")
    )
    assert len(migration_sources) == 1
    migration_before = migration_sources[0].read_bytes()
    assert core_before.rstrip() in migration_before
    [migration_event] = parse_source_file(migration_sources[0], root / "sources")
    assert migration_event.principal_id == owner_principal
    assert re.fullmatch(r"owner/install/[0-9a-f]{16}", migration_event.principal_id)

    agent = _WriterAgent()
    monkeypatch.setattr(writing, "_agent", lambda _model=None: agent)
    report = writing.backfill(root, batch_token_budget=10_000)

    assert report["remaining"] == 0
    assert migration_sources[0].read_bytes() == migration_before
    for path in (root / "topics/core.md", root / "core.md"):
        assert "the only legacy core copy" in path.read_text(encoding="utf-8")


def test_memory_backfill_cli_reports_counts_and_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from openprogram import cli
    from openprogram.memory import store
    from openprogram.memory import writing

    root = tmp_path / "memory"
    report = {
        "status": "ok",
        "candidates": 7,
        "processed": 7,
        "remaining": 0,
        "revision": "a" * 32,
    }
    seen = {}
    monkeypatch.setattr(store, "ensure", lambda: root)
    monkeypatch.setattr(
        writing,
        "backfill",
        lambda memory_dir, *, model=None: seen.update(
            {"root": memory_dir, "model": model}
        ) or report,
    )
    monkeypatch.setattr(
        sys, "argv", ["openprogram", "memory", "backfill", "--model", "writer"],
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out) == report
    assert seen == {"root": root, "model": "writer"}
