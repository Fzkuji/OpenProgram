"""memory_update's declared sources match what the transaction accepts, so a
model writing from the schema alone gets the call right the first time."""

from __future__ import annotations

from contextlib import closing

import pytest


def _items():
    from openprogram.programs.tools.knowledge.memory.memory import UPDATE_SPEC

    record = UPDATE_SPEC["parameters"]["properties"]["memory_changes"]["items"]
    return record["properties"]["sources"]["items"]


def test_a_source_names_a_chat_message_and_a_label():
    items = _items()
    assert set(items["required"]) == {"source", "label"}
    assert items["additionalProperties"] is False
    # The description states both forms the transaction accepts.
    description = items["properties"]["source"]["description"]
    assert "/s/<session>#<message>" in description
    assert "current" in description


@pytest.mark.parametrize("source", ["/s/s1#m1", "openprogram/s1/m1", "current"])
def test_a_source_written_from_the_schema_is_accepted(tmp_path, chats, source):
    from openprogram.state.memory.management import MemoryWorkspace

    asked = chats.say("s1", "m1", "以后就按照这个格式给我写。")
    with closing(MemoryWorkspace(tmp_path / "memory")) as space:
        space.update(
            memory_changes=[{
                "op": "create_record", "content": "The owner fixed the report format.",
                "time": "undated",
                "sources": [{"source": source, "label": "报告格式"}],
                "destination": {
                    "topic_path": "topics/format.md", "headings": [],
                    "position": "end",
                },
            }],
            current_ref=asked, git_commit="off",
        )
    assert "[报告格式](/s/s1#m1)" in (tmp_path / "memory/topics/format.md").read_text()


@pytest.mark.parametrize("source", [
    {"source": "format-owner", "speaker": "owner/local", "quote": "x"},
    {"label": "new-source-a", "role": "user", "content": "x"},
])
def test_the_shapes_models_guessed_are_outside_the_schema(source):
    items = _items()
    assert not set(source) <= set(items["properties"]) or not set(items["required"]) <= set(source)
