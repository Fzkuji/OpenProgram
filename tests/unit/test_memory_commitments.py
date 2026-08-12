from __future__ import annotations

from contextlib import closing
import asyncio
import json
import subprocess

import pytest


def _source(
    memory_dir,
    *,
    trust_state: str = "trusted",
    thread_id: str = "session-1",
    message_id: str = "message-1",
    content: str = "I will submit the rebuttal by Wednesday.",
    speaker_id: str = "owner/local",
) -> str:
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.runtime.state import SourceRecord

    record = SourceRecord(
        provider="openprogram",
        thread_id=thread_id,
        message_id=message_id,
        ordinal=1,
        role="user",
        content=content,
        timestamp="2026-08-11T09:00:00+08:00",
        speaker_id=speaker_id,
        speaker_display="Owner",
        speaker_kind="owner",
        principal_id="owner/install/0123456789abcdef",
        authority_tier="owner",
        trust_state=trust_state,
    )
    with closing(MemoryWorkspace(memory_dir)) as workspace:
        workspace.archive_source_records([record])
    return record.source_id


def test_upsert_commitment_derives_speaker_and_stable_id_from_trusted_source(
    tmp_path,
):
    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )

    source = _source(tmp_path)
    item = {
        "text": "Submit the rebuttal.",
        "due": "2026-08-12",
        "source": source,
        "source_quote": "I will submit the rebuttal by Wednesday.",
    }

    first = upsert_commitments(tmp_path, [item])
    first[0]["status"] = "done"
    first[0]["status_source"] = "owner/manual"
    first[0]["status_changed_at"] = "2026-08-11T01:00:00+00:00"
    first[0]["notification_steps"] = ["due"]
    (tmp_path / "commitments.jsonl").write_text(
        __import__("json").dumps(first[0]) + "\n", encoding="utf-8"
    )
    second = upsert_commitments(tmp_path, [item])

    assert second == load_commitments(tmp_path)
    assert second == [
        {
            "id": first[0]["id"],
            "text": "Submit the rebuttal.",
            "due": "2026-08-12",
            "speaker_id": "owner/local",
            "source": source,
            "source_quote": "I will submit the rebuttal by Wednesday.",
            "status": "done",
            "status_source": "owner/manual",
            "status_source_quote": None,
            "status_changed_at": "2026-08-11T01:00:00+00:00",
            "notification_steps": ["due"],
        }
    ]
    assert (tmp_path / "commitments.jsonl").stat().st_mode & 0o777 == 0o600


def test_upsert_commitment_rejects_pending_source(tmp_path):
    from openprogram.memory.runtime.commitments import upsert_commitments

    source = _source(tmp_path, trust_state="pending")

    with pytest.raises(ValueError, match="trusted"):
        upsert_commitments(
            tmp_path,
            [
                {
                    "text": "Submit the rebuttal.",
                    "due": "2026-08-12",
                    "source": source,
                    "source_quote": "I will submit the rebuttal by Wednesday.",
                }
            ],
        )


def test_commitment_id_uses_exact_source_evidence_not_llm_text_or_item_order(
    tmp_path,
):
    from openprogram.memory.runtime.commitments import upsert_commitments

    source = _source(
        tmp_path,
        content=(
            "I will submit the rebuttal by Wednesday. "
            "I will upload the appendix tomorrow."
        ),
    )
    submit = {
        "text": "Submit the rebuttal.",
        "due": "2026-08-12",
        "source": source,
        "source_quote": "I will submit the rebuttal by Wednesday.",
    }
    upload = {
        "text": "Upload the appendix.",
        "due": "2026-08-12",
        "source": source,
        "source_quote": "I will upload the appendix tomorrow.",
    }

    first = upsert_commitments(tmp_path, [submit, upload])
    second = upsert_commitments(
        tmp_path,
        [upload, {**submit, "text": "Submit rebuttal."}],
    )

    assert len(second) == 2
    assert {row["id"] for row in second} == {row["id"] for row in first}
    assert {row["source_quote"] for row in second} == {
        submit["source_quote"],
        upload["source_quote"],
    }


def test_commitment_requires_exact_quote_from_trusted_source(tmp_path):
    from openprogram.memory.runtime.commitments import upsert_commitments

    source = _source(tmp_path)

    with pytest.raises(ValueError, match="exact substring"):
        upsert_commitments(
            tmp_path,
            [
                {
                    "text": "Submit the rebuttal.",
                    "due": None,
                    "source": source,
                    "source_quote": "I might submit the rebuttal.",
                }
            ],
        )


def test_transition_commitment_changes_only_open_records(tmp_path):
    from openprogram.memory.runtime.commitments import (
        transition_commitments,
        upsert_commitments,
    )

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": None,
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]

    changed = transition_commitments(
        tmp_path,
        [{"id": row["id"], "status": "dismissed"}],
        manual_source="owner/manual",
    )

    assert changed[0]["status"] == "dismissed"
    assert changed[0]["status_source"] == "owner/manual"
    assert changed[0]["status_source_quote"] is None
    assert changed[0]["status_changed_at"].endswith("+00:00")
    with pytest.raises(ValueError, match="open commitment"):
        transition_commitments(
            tmp_path,
            [{"id": row["id"], "status": "done"}],
            manual_source="owner/manual",
        )


def test_writer_tool_stages_and_commits_only_batch_sources(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import load_commitments

    source = _source(tmp_path)
    with closing(
        MemoryWorkspace(
            tmp_path,
            allowed_new_source_refs={source},
        )
    ) as workspace:
        baseline = workspace.baseline()
        audit = []
        tool = next(
            item
            for item in management_tools(workspace, audit)
            if item.name == "record_commitments"
        )
        result = asyncio.run(
            tool.handler(
                {
                    "commitments": [
                        {
                            "text": "Submit the rebuttal.",
                            "due": "2026-08-12",
                            "source": source,
                            "source_quote": "I will submit the rebuttal by Wednesday.",
                        }
                    ]
                }
            )
        )
        assert result["is_error"] is False, result
        assert load_commitments(tmp_path) == []
        workspace.commit_edits(*baseline)

    assert load_commitments(tmp_path)[0]["text"] == "Submit the rebuttal."


def test_writer_schema_declares_commitment_batch_and_item_limits(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import (
        MAX_COMMITMENT_BATCH_ITEMS,
        MAX_COMMITMENT_QUOTE_CHARS,
        MAX_COMMITMENT_SOURCE_CHARS,
        MAX_COMMITMENT_TEXT_CHARS,
    )

    with closing(MemoryWorkspace(tmp_path)) as workspace:
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )
    properties = tool.input_schema["properties"]

    assert properties["commitments"]["maxItems"] == MAX_COMMITMENT_BATCH_ITEMS
    assert properties["transitions"]["maxItems"] == MAX_COMMITMENT_BATCH_ITEMS
    commitment = properties["commitments"]["items"]["properties"]
    transition = properties["transitions"]["items"]["properties"]
    assert commitment["text"]["maxLength"] == MAX_COMMITMENT_TEXT_CHARS
    assert commitment["source"]["maxLength"] == MAX_COMMITMENT_SOURCE_CHARS
    assert commitment["source_quote"]["maxLength"] == MAX_COMMITMENT_QUOTE_CHARS
    assert transition["source"]["maxLength"] == MAX_COMMITMENT_SOURCE_CHARS
    assert transition["source_quote"]["maxLength"] == MAX_COMMITMENT_QUOTE_CHARS


def test_runtime_rejects_oversized_commitment_batches_and_fields(tmp_path):
    from openprogram.memory.runtime.commitments import (
        MAX_COMMITMENT_BATCH_ITEMS,
        MAX_COMMITMENT_QUOTE_CHARS,
        MAX_COMMITMENT_TEXT_CHARS,
        upsert_commitments,
    )

    source = _source(tmp_path)
    valid = {
        "text": "Submit the rebuttal.",
        "due": "2026-08-12",
        "source": source,
        "source_quote": "I will submit the rebuttal by Wednesday.",
    }

    with pytest.raises(ValueError, match="at most"):
        upsert_commitments(tmp_path, [valid] * (MAX_COMMITMENT_BATCH_ITEMS + 1))
    with pytest.raises(ValueError, match="text exceeds"):
        upsert_commitments(
            tmp_path,
            [{**valid, "text": "x" * (MAX_COMMITMENT_TEXT_CHARS + 1)}],
        )

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        upsert_commitments(tmp_path, [{**valid, "due": "20260812"}])

    overlong_speaker_source = _source(
        tmp_path,
        message_id="message-speaker-long",
        speaker_id="x" * 513,
    )
    with pytest.raises(ValueError, match="speaker identity"):
        upsert_commitments(
            tmp_path,
            [{**valid, "source": overlong_speaker_source}],
        )

    long_quote = "x" * (MAX_COMMITMENT_QUOTE_CHARS + 1)
    long_source = _source(
        tmp_path,
        message_id="message-long",
        content=long_quote,
    )
    with pytest.raises(ValueError, match="source_quote exceeds"):
        upsert_commitments(
            tmp_path,
            [
                {
                    "text": "Bounded item.",
                    "due": None,
                    "source": long_source,
                    "source_quote": long_quote,
                }
            ],
        )


def test_runtime_limits_writer_transitions_and_combined_tool_batch(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import (
        MAX_COMMITMENT_BATCH_ITEMS,
        MAX_COMMITMENT_QUOTE_CHARS,
        transition_commitments,
        upsert_commitments,
    )

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": None,
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    with pytest.raises(ValueError, match="at most"):
        transition_commitments(
            tmp_path,
            [{"id": row["id"], "status": "done"}] * (MAX_COMMITMENT_BATCH_ITEMS + 1),
            manual_source="owner/manual",
        )

    long_quote = "x" * (MAX_COMMITMENT_QUOTE_CHARS + 1)
    closure = _source(
        tmp_path,
        message_id="message-closure-long",
        content=long_quote,
    )
    with pytest.raises(ValueError, match="source_quote exceeds"):
        transition_commitments(
            tmp_path,
            [
                {
                    "id": row["id"],
                    "status": "done",
                    "source": closure,
                    "source_quote": long_quote,
                }
            ],
        )

    with closing(MemoryWorkspace(tmp_path)) as workspace:
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )
        result = asyncio.run(
            tool.handler(
                {
                    "commitments": [{}] * 33,
                    "transitions": [{}] * 32,
                }
            )
        )
    assert result["is_error"] is True
    assert "at most 64" in result["content"][0]["text"]


def test_writer_combined_batch_failure_restores_stage_and_committed_state(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import upsert_commitments

    original = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": original,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    closure = _source(
        tmp_path,
        message_id="message-2",
        content="I will upload the appendix tomorrow.",
    )
    committed_path = tmp_path / "commitments.jsonl"
    committed_before = committed_path.read_bytes()

    with closing(
        MemoryWorkspace(tmp_path, allowed_new_source_refs={closure})
    ) as workspace:
        baseline = workspace.baseline()
        staged_path = workspace.stage_dir / "commitments.jsonl"
        staged_before = staged_path.read_bytes()
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )

        result = asyncio.run(
            tool.handler(
                {
                    "commitments": [
                        {
                            "text": "Upload the appendix.",
                            "due": "2026-08-13",
                            "source": closure,
                            "source_quote": "I will upload the appendix tomorrow.",
                        }
                    ],
                    "transitions": [
                        {
                            "id": "com_0000000000000000",
                            "status": "done",
                            "source": closure,
                            "source_quote": "I will upload the appendix tomorrow.",
                        }
                    ],
                }
            )
        )

        assert result["is_error"] is True
        assert staged_path.read_bytes() == staged_before
        workspace.commit_edits(*baseline)

    assert committed_path.read_bytes() == committed_before


def test_writer_rollback_failure_makes_partial_stage_uncommittable(
    tmp_path, monkeypatch
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import upsert_commitments
    from openprogram.store.session import git_session

    original = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": original,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    closure = _source(
        tmp_path,
        message_id="message-2",
        content="I will upload the appendix tomorrow.",
    )
    committed_path = tmp_path / "commitments.jsonl"
    committed_before = committed_path.read_bytes()

    with closing(
        MemoryWorkspace(tmp_path, allowed_new_source_refs={closure})
    ) as workspace:
        baseline = workspace.baseline()
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )
        real_atomic_write_text = git_session.atomic_write_text
        real_discard_stage = workspace._discard_stage
        calls = 0
        discard_calls = 0

        def fail_rollback(path, text):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("rollback failed")
            return real_atomic_write_text(path, text)

        def fail_first_discard():
            nonlocal discard_calls
            discard_calls += 1
            if discard_calls == 1:
                raise OSError("discard failed")
            return real_discard_stage()

        monkeypatch.setattr(git_session, "atomic_write_text", fail_rollback)
        monkeypatch.setattr(workspace, "_discard_stage", fail_first_discard)
        result = asyncio.run(
            tool.handler(
                {
                    "commitments": [
                        {
                            "text": "Upload the appendix.",
                            "due": "2026-08-13",
                            "source": closure,
                            "source_quote": "I will upload the appendix tomorrow.",
                        }
                    ],
                    "transitions": [
                        {
                            "id": "com_0000000000000000",
                            "status": "done",
                            "source": closure,
                            "source_quote": "I will upload the appendix tomorrow.",
                        }
                    ],
                }
            )
        )

        assert result["is_error"] is True
        assert workspace._stage_usable is False
        with pytest.raises(RuntimeError, match="stage is unavailable"):
            workspace.commit_edits(*baseline)

    assert committed_path.read_bytes() == committed_before


def test_failed_stage_refresh_stays_uncommittable(tmp_path, monkeypatch):
    import shutil

    from openprogram.memory.management import MemoryWorkspace

    _source(tmp_path)
    (tmp_path / "commitments.jsonl").write_text("")
    workspace = MemoryWorkspace(tmp_path)
    baseline = workspace.baseline()
    real_copy2 = shutil.copy2
    calls = 0

    def fail_first_copy(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("copy failed")
        return real_copy2(source, target)

    monkeypatch.setattr(shutil, "copy2", fail_first_copy)
    with pytest.raises(OSError, match="copy failed"):
        workspace._refresh_stage()
    with pytest.raises(RuntimeError, match="stage is unavailable"):
        workspace.commit_edits(*baseline)

    workspace._refresh_stage()
    workspace.commit_edits(*baseline)
    workspace.close()


def test_commit_cancel_during_backup_restores_workspace_and_poisons_stage(
    tmp_path, monkeypatch
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management import block_views
    from openprogram.memory.runtime.commitments import load_commitments, upsert_commitments

    source = _source(tmp_path)
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "note.md").write_text("# Note\n", encoding="utf-8")
    (tmp_path / "commitments.jsonl").write_text("", encoding="utf-8")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    workspace = MemoryWorkspace(tmp_path)
    baseline = workspace.baseline()
    upsert_commitments(
        workspace.stage_dir,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
        source_memory_dir=tmp_path,
    )
    real_replace = block_views.os.replace
    real_rmtree = block_views.shutil.rmtree
    cancelled = KeyboardInterrupt("cancel install")
    replace_calls = 0

    def cancel_second_replace(source_path, target_path):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise cancelled
        return real_replace(source_path, target_path)

    def fail_backup_cleanup(path, *args, **kwargs):
        if path.name.endswith("-block-backup"):
            raise OSError("cleanup failed")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(block_views.os, "replace", cancel_second_replace)
    monkeypatch.setattr(block_views.shutil, "rmtree", fail_backup_cleanup)
    with pytest.raises(KeyboardInterrupt) as caught:
        workspace.commit_edits(*baseline)

    assert caught.value is cancelled
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and "-block-backup" not in path.parts
    } == before
    assert workspace._stage_usable is False
    with pytest.raises(RuntimeError, match="stage is unavailable"):
        workspace.commit_edits(*baseline)
    assert load_commitments(tmp_path) == []
    monkeypatch.undo()
    workspace.close()


def test_writer_tool_rejects_source_outside_selected_batch(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools

    source = _source(tmp_path)
    with closing(
        MemoryWorkspace(
            tmp_path,
            allowed_new_source_refs={"openprogram/other/message"},
        )
    ) as workspace:
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )
        result = asyncio.run(
            tool.handler(
                {
                    "commitments": [
                        {
                            "text": "Submit the rebuttal.",
                            "due": "2026-08-12",
                            "source": source,
                            "source_quote": "I will submit the rebuttal by Wednesday.",
                        }
                    ]
                }
            )
        )

    assert result["is_error"] is True
    assert "selected writer batch" in result["content"][0]["text"]


def test_writer_transition_requires_batch_evidence_and_persists_provenance(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.tools import management_tools
    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )

    original = _source(tmp_path)
    closure = _source(
        tmp_path,
        thread_id="session-1",
        message_id="message-2",
        content="I submitted the rebuttal this morning.",
    )
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": original,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    with closing(
        MemoryWorkspace(tmp_path, allowed_new_source_refs={closure})
    ) as workspace:
        baseline = workspace.baseline()
        tool = next(
            item
            for item in management_tools(workspace, [])
            if item.name == "record_commitments"
        )
        missing = asyncio.run(
            tool.handler({"transitions": [{"id": row["id"], "status": "done"}]})
        )
        assert missing["is_error"] is True
        assert "source" in missing["content"][0]["text"]

        result = asyncio.run(
            tool.handler(
                {
                    "transitions": [
                        {
                            "id": row["id"],
                            "status": "done",
                            "source": closure,
                            "source_quote": "I submitted the rebuttal this morning.",
                        }
                    ]
                }
            )
        )
        assert result["is_error"] is False, result
        workspace.commit_edits(*baseline)

    changed = load_commitments(tmp_path)[0]
    assert changed["status"] == "done"
    assert changed["status_source"] == closure
    assert changed["status_source_quote"] == ("I submitted the rebuttal this morning.")
    assert changed["status_changed_at"].endswith("+00:00")


def test_writer_task_exposes_open_commitments_for_later_closure(tmp_path):
    from openprogram.memory.management.api import render_writer_task
    from openprogram.memory.runtime.commitments import upsert_commitments

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]

    rendered = render_writer_task([], memory_dir=tmp_path)

    assert row["id"] in rendered
    assert row["text"] in rendered


def test_status_exposes_sanitized_commitment_counts_and_records(tmp_path):
    from openprogram.memory.retrieval import inspect
    from openprogram.memory.runtime.commitments import upsert_commitments

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    row["private_channel_metadata"] = {"token": "must-not-leak"}
    (tmp_path / "commitments.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )

    status = inspect.status(tmp_path)

    assert status["commitments"]["counts"] == {
        "total": 1,
        "open": 1,
        "done": 0,
        "dismissed": 0,
    }
    assert status["commitments"]["records"] == [
        {
            "id": row["id"],
            "text": "Submit the rebuttal.",
            "due": "2026-08-12",
            "speaker_id": "owner/local",
            "source": source,
            "status": "open",
            "status_source": None,
            "status_changed_at": None,
            "notification_steps": [],
        }
    ]
    assert "I will submit the rebuttal by Wednesday." not in json.dumps(status)
    assert "private_channel_metadata" not in json.dumps(status)


def test_memory_update_transitions_commitment_without_topic_patch(
    tmp_path,
    monkeypatch,
):
    import json

    import openprogram.paths as paths
    from openprogram.functions.tools.memory import memory as memory_tools
    from openprogram.memory import store
    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    root = store.ensure()
    source = _source(root)
    row = upsert_commitments(
        root,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    monkeypatch.setattr(
        memory_tools,
        "authority_from_message",
        lambda *_: {
            "speaker_kind": "owner",
            "speaker_id": "owner/local",
            "speaker_display": "Owner",
            "principal_id": "owner/install/0123456789abcdef",
            "authority_tier": "owner",
            "interaction": "interactive",
        },
    )

    revision = json.loads(memory_tools.memory_status())["revision"]
    result = json.loads(
        memory_tools.memory_update(
            base_revision=revision,
            commitment_transitions=[{"id": row["id"], "status": "done"}],
        )
    )

    assert result["ok"] is True, result
    assert "commitments.jsonl" in result["changed_files"]
    changed = load_commitments(root)[0]
    assert changed["status"] == "done"
    assert changed["status_source"] == "owner/manual"
    assert changed["status_source_quote"] is None
    assert changed["status_changed_at"].endswith("+00:00")


def test_memory_update_rejects_commitment_transition_from_paired_turn(
    tmp_path,
    monkeypatch,
):
    import json

    import openprogram.paths as paths
    from openprogram.functions.tools.memory import memory as memory_tools
    from openprogram.memory import store
    from openprogram.memory.runtime.commitments import upsert_commitments

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    root = store.ensure()
    source = _source(root)
    row = upsert_commitments(
        root,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    monkeypatch.setattr(
        memory_tools,
        "authority_from_message",
        lambda *_: {
            "speaker_kind": "human",
            "speaker_id": "telegram/42",
            "speaker_display": "Paired",
            "principal_id": "paired/telegram/42",
            "authority_tier": "paired",
            "interaction": "interactive",
        },
    )

    revision = json.loads(memory_tools.memory_status())["revision"]
    result = json.loads(
        memory_tools.memory_update(
            base_revision=revision,
            commitment_transitions=[{"id": row["id"], "status": "done"}],
        )
    )

    assert result["ok"] is False
    assert "owner" in result["error"]["message"]


def test_heartbeat_sends_due_commitment_once_and_retries_only_after_escalation(
    tmp_path,
):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import upsert_commitments
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    sent = []
    target = ("telegram", "default", "42")

    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: target,
            send=lambda got_target, text: sent.append((got_target, text)) or True,
        )
        == 1
    )
    assert sent[0][0] == target
    assert "Submit the rebuttal." in sent[0][1]
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 13, 9, 0),
            target_for_source=lambda _source: target,
            send=lambda got_target, text: sent.append((got_target, text)) or True,
        )
        == 0
    )
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 19, 9, 0),
            target_for_source=lambda _source: target,
            send=lambda got_target, text: sent.append((got_target, text)) or True,
        )
        == 1
    )
    assert len(sent) == 2


def test_heartbeat_first_observation_after_escalation_stays_monotonic(tmp_path):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-01",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    sent: list[str] = []

    def send(_target, text: str) -> bool:
        sent.append(text)
        return True

    kwargs = {
        "target_for_source": lambda _source: ("telegram", "default", "42"),
        "send": send,
    }
    assert run_heartbeat(tmp_path, now=datetime(2026, 8, 12, 9, 0), **kwargs) == 1
    assert "due)" in sent[-1]
    assert "overdue)" not in sent[-1]
    assert load_commitments(tmp_path)[0]["notification_steps"] == ["due"]

    assert run_heartbeat(tmp_path, now=datetime(2026, 8, 12, 10, 0), **kwargs) == 1
    assert "overdue)" in sent[-1]
    assert load_commitments(tmp_path)[0]["notification_steps"] == [
        "due",
        "overdue:7",
    ]
    assert run_heartbeat(tmp_path, now=datetime(2026, 8, 12, 11, 0), **kwargs) == 0


def test_heartbeat_does_not_downgrade_persisted_overdue_only_state(tmp_path):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-01",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    row["notification_steps"] = ["overdue:7"]
    path = tmp_path / "commitments.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()

    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: ("telegram", "default", "42"),
            send=lambda *_args: pytest.fail("overdue state must not regress to due"),
        )
        == 0
    )
    assert path.read_bytes() == before
    assert load_commitments(tmp_path)[0]["notification_steps"] == ["overdue:7"]


def test_heartbeat_quiet_hours_and_send_failure_do_not_consume_notification(
    tmp_path,
):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    target = ("telegram", "default", "42")

    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 23, 30),
            target_for_source=lambda _source: target,
            send=lambda *_args: True,
        )
        == 0
    )
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 13, 9, 0),
            target_for_source=lambda _source: target,
            send=lambda *_args: False,
        )
        == 0
    )
    assert load_commitments(tmp_path)[0]["notification_steps"] == []
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 13, 9, 1),
            target_for_source=lambda _source: target,
            send=lambda *_args: True,
        )
        == 1
    )


def test_heartbeat_skips_invalid_rows_without_suppressing_valid_reminders(
    tmp_path,
):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        commitment_status,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    valid = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    invalid = {**valid, "id": "com_1111111111111111", "due": "next week"}
    with (tmp_path / "commitments.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(invalid) + "\n")
    sent = []

    assert commitment_status(tmp_path)["counts"]["invalid"] == 1
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: ("telegram", "default", "42"),
            send=lambda target, text: sent.append((target, text)) or True,
        )
        == 1
    )
    assert len(sent) == 1
    assert "Submit the rebuttal." in sent[0][1]
    after = commitment_status(tmp_path)
    assert after["counts"]["invalid"] == 1
    assert after["records"][0]["notification_steps"] == ["due"]


def test_mutations_refuse_invalid_file_without_deleting_bad_rows(tmp_path):
    from openprogram.memory.runtime.commitments import (
        transition_commitments,
        upsert_commitments,
    )

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    path = tmp_path / "commitments.jsonl"
    path.write_text(path.read_text() + "not-json\n", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="invalid commitment"):
        upsert_commitments(
            tmp_path,
            [
                {
                    "text": "Submit it.",
                    "due": None,
                    "source": source,
                    "source_quote": "I will submit the rebuttal by Wednesday.",
                }
            ],
        )
    assert path.read_bytes() == before

    with pytest.raises(ValueError, match="invalid commitment"):
        transition_commitments(
            tmp_path,
            [{"id": row["id"], "status": "done"}],
            manual_source="owner/manual",
        )
    assert path.read_bytes() == before


def test_persisted_overlong_record_is_invalid_skipped_and_blocks_mutation(
    tmp_path,
):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        MAX_COMMITMENT_TEXT_CHARS,
        commitment_status,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]
    row["text"] = "x" * (MAX_COMMITMENT_TEXT_CHARS + 1)
    path = tmp_path / "commitments.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()

    assert commitment_status(tmp_path) == {
        "counts": {
            "total": 0,
            "open": 0,
            "done": 0,
            "dismissed": 0,
            "invalid": 1,
        },
        "records": [],
    }
    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: ("telegram", "default", "42"),
            send=lambda *_args: pytest.fail("invalid record must not send"),
        )
        == 0
    )
    with pytest.raises(ValueError, match="invalid commitment"):
        upsert_commitments(
            tmp_path,
            [
                {
                    "text": "Submit the rebuttal.",
                    "due": None,
                    "source": source,
                    "source_quote": "I will submit the rebuttal by Wednesday.",
                }
            ],
        )
    assert path.read_bytes() == before


def test_heartbeat_groups_by_target_and_keeps_missing_targets_visible(tmp_path):
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    routed_source = _source(
        tmp_path,
        content=(
            "I will submit the rebuttal by Wednesday. "
            "I will upload the appendix tomorrow."
        ),
    )
    missing_source = _source(
        tmp_path,
        thread_id="session-2",
        message_id="message-2",
        content="I will email the slides tomorrow.",
    )
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": routed_source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            },
            {
                "text": "Upload the appendix.",
                "due": "2026-08-12",
                "source": routed_source,
                "source_quote": "I will upload the appendix tomorrow.",
            },
            {
                "text": "Email the slides.",
                "due": "2026-08-12",
                "source": missing_source,
                "source_quote": "I will email the slides tomorrow.",
            },
        ],
    )
    sent = []
    target = ("telegram", "default", "42")

    count = run_heartbeat(
        tmp_path,
        now=datetime(2026, 8, 12, 9, 0),
        target_for_source=lambda source: target if source == routed_source else None,
        send=lambda got_target, text: sent.append((got_target, text)) or True,
    )

    assert count == 2
    assert len(sent) == 1
    assert sent[0][0] == target
    assert "Submit the rebuttal." in sent[0][1]
    assert "Upload the appendix." in sent[0][1]
    rows = {row["text"]: row for row in load_commitments(tmp_path)}
    assert rows["Email the slides."]["status"] == "open"
    assert rows["Email the slides."]["notification_steps"] == []


def test_source_target_resolution_uses_current_session_binding(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    from openprogram.proactive.heartbeat import target_for_source

    db = SessionDB(tmp_path / "sessions")
    db.create_session(
        "session-1",
        "main",
        channel="telegram",
        account_id="default",
        peer_id="42",
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)

    assert target_for_source("openprogram/session-1/message-1") == (
        "telegram",
        "default",
        "42",
    )
    db.update_session(
        "session-1",
        channel="discord",
        account_id="work",
        peer_id="99",
    )
    assert target_for_source("openprogram/session-1/message-1") == (
        "discord",
        "work",
        "99",
    )
    assert target_for_source("openprogram/missing/message") is None


def test_cron_tick_routes_heartbeat_through_source_session_binding(
    tmp_path,
    monkeypatch,
):
    from datetime import datetime

    import openprogram.paths as paths
    from openprogram.agent.session_db import SessionDB
    from openprogram.functions.tools.cron import worker
    from openprogram.memory import store
    from openprogram.memory.runtime.commitments import upsert_commitments

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    root = store.ensure()
    source = _source(root)
    upsert_commitments(
        root,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )
    db = SessionDB(tmp_path / "sessions")
    db.create_session(
        "session-1",
        "main",
        channel="telegram",
        account_id="default",
        peer_id="42",
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {
            "proactive": {
                "heartbeat": "daily",
                "quiet_hours": "23:00-08:00",
            }
        },
    )
    monkeypatch.setattr(worker, "_load", lambda _path: [])
    sent = []
    monkeypatch.setattr(
        "openprogram.channels.outbound.send",
        lambda channel, account, peer, text: (
            sent.append((channel, account, peer, text)) or True
        ),
    )

    assert worker._tick({}, now=datetime(2026, 8, 12, 9, 0)) == 0
    assert sent == [
        (
            "telegram",
            "default",
            "42",
            "Commitment reminder:\n- Submit the rebuttal. (due 2026-08-12, due)",
        )
    ]


def test_cron_heartbeat_invalid_config_fails_without_sending(
    tmp_path,
    monkeypatch,
):
    from datetime import datetime

    import openprogram.paths as paths
    from openprogram.functions.tools.cron import worker

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(worker, "_load", lambda _path: [])
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {
            "proactive": {
                "heartbeat": "daily",
                "quiet_hours": "invalid",
            }
        },
    )
    monkeypatch.setattr(
        "openprogram.channels.outbound.send",
        lambda *_args: pytest.fail("invalid config must not send"),
    )

    assert worker._tick({}, now=datetime(2026, 8, 12, 9, 0)) == 0


def test_memory_git_commit_includes_commitment_records(tmp_path):
    from openprogram.memory.runtime.commitments import upsert_commitments
    from openprogram.memory.runtime.state import RuntimeStateStore

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    source = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )

    assert RuntimeStateStore(tmp_path).git_commit("memory: test")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "commitments.jsonl"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr


@pytest.mark.parametrize(
    ("cadence", "stamp", "expected"),
    [
        ("off", "2026-08-12T09:00:00", False),
        ("daily", "2026-08-12T09:00:00", True),
        ("daily", "2026-08-12T10:00:00", False),
        ("hourly", "2026-08-12T10:00:00", True),
        ("hourly", "2026-08-12T10:01:00", False),
    ],
)
def test_heartbeat_cadence(cadence, stamp, expected):
    from datetime import datetime

    from openprogram.proactive.heartbeat import cadence_due

    assert cadence_due(cadence, datetime.fromisoformat(stamp)) is expected


def test_quiet_hours_rejects_seconds_and_offsets():
    from datetime import datetime

    from openprogram.proactive.heartbeat import in_quiet_hours

    for value in ("23:00:30-08:00", "23:00+01:00-08:00"):
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            in_quiet_hours(datetime(2026, 8, 12, 23, 30), value)


def test_heartbeat_releases_workspace_lock_while_sending(tmp_path):
    """A slow channel send must not stall every other memory write."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime

    from openprogram.memory.management.transaction import workspace_write_lock
    from openprogram.memory.runtime.commitments import upsert_commitments
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )

    def _send(_target, _text):
        def _contend_for_lock():
            with workspace_write_lock(tmp_path, timeout_s=0.5):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_contend_for_lock).result(timeout=1)

    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: ("telegram", "default", "42"),
            send=_send,
        )
        == 1
    )


def test_heartbeat_preserves_transition_committed_during_send(tmp_path):
    """Recording a delivered step must not revert a concurrent transition."""
    from datetime import datetime

    from openprogram.memory.runtime.commitments import (
        load_commitments,
        transition_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]

    def _send(_target, _text):
        transition_commitments(
            tmp_path,
            [{"id": row["id"], "status": "done"}],
            manual_source="owner/manual",
        )
        return True

    assert (
        run_heartbeat(
            tmp_path,
            now=datetime(2026, 8, 12, 9, 0),
            target_for_source=lambda _source: ("telegram", "default", "42"),
            send=_send,
        )
        == 1
    )
    stored = load_commitments(tmp_path)[0]
    assert stored["status"] == "done"
    assert stored["notification_steps"] == ["due"]


def test_writer_commit_rejects_stale_stage_after_heartbeat_update(tmp_path):
    """A writer snapshot must not overwrite a delivered heartbeat step."""
    from contextlib import closing
    from datetime import datetime

    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError
    from openprogram.memory.runtime.commitments import (
        load_commitments,
        upsert_commitments,
    )
    from openprogram.proactive.heartbeat import run_heartbeat

    source = _source(tmp_path)
    row = upsert_commitments(
        tmp_path,
        [
            {
                "text": "Submit the rebuttal.",
                "due": "2026-08-12",
                "source": source,
                "source_quote": "I will submit the rebuttal by Wednesday.",
            }
        ],
    )[0]

    with closing(MemoryWorkspace(tmp_path)) as workspace:
        baseline = workspace.baseline()
        staged = load_commitments(workspace.stage_dir)
        staged[0]["text"] = "Submit the revised rebuttal."
        from openprogram.memory.runtime.commitments import _write

        _write(workspace.stage_dir, staged)

        assert (
            run_heartbeat(
                tmp_path,
                now=datetime(2026, 8, 12, 9, 0),
                target_for_source=lambda _source: ("telegram", "default", "42"),
                send=lambda _target, _text: True,
            )
            == 1
        )

        with pytest.raises(TransactionError, match="workspace changed"):
            workspace.commit_edits(*baseline)

    stored = load_commitments(tmp_path)[0]
    assert stored["id"] == row["id"]
    assert stored["text"] == "Submit the rebuttal."
    assert stored["notification_steps"] == ["due"]
