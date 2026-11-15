"""What history keeps beside a tool result, and the limits on what a request carries.

* The shared ``tool_results/`` is collected by reference, failing closed.
* Images a turn sent are kept by content and put back where they were.
* A long run of images is cut once past 25 MiB, back to 15 MiB.
* A person's message is never cut; past 1 MiB characters it is refused.
* The card shows the tool's own output and reads the saved whole text.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.models.providers.shared import media_budget
from openprogram.models.providers.types import ImageContent, TextContent, ToolResultMessage, UserMessage
from openprogram.state.store.session import media, tool_output_gc


# --- the shared tool_results/ -------------------------------------------


def _old(path: Path, text: str = "x", age: float = tool_output_gc.GRACE_SECONDS + 60) -> Path:
    path.write_text(text)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _results(tmp_path: Path) -> Path:
    directory = tmp_path / "tool_results"
    directory.mkdir()
    return directory


def _session(tmp_path: Path, name: str, record: str) -> Path:
    folder = tmp_path / "chats" / name
    folder.mkdir(parents=True)
    (folder / "history.jsonl").write_text(record)
    return folder


def test_a_named_file_is_kept_and_an_unnamed_old_one_removed(tmp_path):
    results = _results(tmp_path)
    kept = _old(results / "aaa.txt")
    gone = _old(results / "bbb.txt")
    folder = _session(tmp_path, "s1", json.dumps({"output": f"saved to {kept}"}))
    assert tool_output_gc.collect(results, [folder]) == [gone]
    assert kept.exists() and not gone.exists()


def test_a_file_younger_than_a_day_is_kept_whatever_names_it(tmp_path):
    results = _results(tmp_path)
    young = _old(results / "young.txt", age=60)
    assert tool_output_gc.collect(results, [_session(tmp_path, "s1", "{}")]) == []
    assert young.exists()


def test_when_any_conversation_cannot_be_read_nothing_is_removed(tmp_path):
    results = _results(tmp_path)
    old = _old(results / "ccc.txt")
    folder = _session(tmp_path, "s1", "{}")
    (folder / "broken.jsonl").write_bytes(b"\xff\xfe\x00bad")
    assert tool_output_gc.collect(results, [folder]) == []
    assert tool_output_gc.collect(results, None) == []
    assert old.exists()


def test_a_windows_path_in_a_json_record_names_its_file(tmp_path):
    results = _results(tmp_path)
    old = _old(results / "ddd.txt")
    record = json.dumps({"output": "C:\\Users\\me\\.openprogram\\tool_results\\ddd.txt"})
    assert "\\\\" in record
    assert tool_output_gc.collect(results, [_session(tmp_path, "s1", record)]) == []
    assert old.exists()


# --- media kept by content ------------------------------------------------


PNG = base64.b64encode(b"\x89PNG fake bytes").decode()


def test_media_is_kept_once_and_put_back_at_its_index(tmp_path):
    blocks = [TextContent(text="before"), ImageContent(data=PNG, mime_type="image/png"),
              TextContent(text="after")]
    records = media.records_for_blocks(tmp_path, blocks)
    again = media.records_for_blocks(tmp_path, blocks)
    assert records == again and len(list((tmp_path / media.DIRNAME).iterdir())) == 1
    assert records[0]["index"] == 1
    content = media.with_media([TextContent(text="before"), TextContent(text="after")], records, tmp_path)
    assert [c.type for c in content] == ["text", "image", "text"] and content[1].data == PNG


def test_a_kept_block_whose_file_is_gone_is_left_out(tmp_path):
    [record] = media.records_for_blocks(tmp_path, [ImageContent(data=PNG, mime_type="image/png")])
    Path(record["path"]).unlink()
    text = [TextContent(text="t")]
    assert media.with_media(text, [record], tmp_path) == text


def test_a_persons_images_are_numbered_after_the_text(tmp_path):
    attachments = [{"type": "image", "data": PNG, "media_type": "image/png"},
                   {"type": "file", "path": "x"},
                   {"type": "image", "data": base64.b64encode(b"other").decode(), "media_type": "image/jpeg"}]
    records = media.records_for_attachments(tmp_path, attachments, 1)
    assert [r["index"] for r in records] == [1, 2]
    assert records[1]["path"].endswith(".jpg")


# --- the image budget ------------------------------------------------------


def _image_message(size: int) -> ToolResultMessage:
    return ToolResultMessage(tool_call_id="c", tool_name="shot", timestamp=0,
                             content=[ImageContent(data="a" * size, mime_type="image/png")])


def _kept(messages) -> list[bool]:
    return [isinstance(m.content[0], ImageContent) for m in messages]


def test_a_run_of_screenshots_is_cut_once_past_the_trigger_back_to_the_target():
    mib = 1024 * 1024
    messages = [_image_message(mib) for _ in range(30)]
    bounded = media_budget.bound_images(messages)
    kept = _kept(bounded)
    assert kept == [False] * kept.index(True) + [True] * (30 - kept.index(True))
    assert sum(kept) * mib <= media_budget.IMAGE_BYTES_TRIGGER and kept.index(True) == 11
    assert bounded[0].content[0].text == media_budget.IMAGE_REMOVED
    # The cut moves only when the kept bytes pass the trigger again: the
    # next few requests send the same prefix.
    cut = kept.index(True)
    for extra in range(1, 6):
        assert _kept(media_budget.bound_images(messages + [_image_message(mib)] * extra))[:cut + 1] \
            == kept[:cut + 1]
    under = [_image_message(mib) for _ in range(20)]
    assert media_budget.bound_images(under) is under


def test_a_persons_images_become_a_note_for_a_model_without_image_input():
    image = ImageContent(data=PNG, mime_type="image/png")
    user = UserMessage(content=[TextContent(text="look"), image], timestamp=0)
    tool = ToolResultMessage(tool_call_id="c", tool_name="shot", content=[image], timestamp=0)
    text_only = SimpleNamespace(input=["text"])
    out = media_budget.without_unsupported_images([user, tool], text_only)
    assert out[0].content[1].text == media_budget.IMAGE_OMITTED
    assert out[1] is tool          # a tool's screenshot is the adapter's to place
    sees = SimpleNamespace(input=["text", "image"])
    assert media_budget.without_unsupported_images([user], sees)[0] is user


# --- a person's message ------------------------------------------------------


def test_a_message_up_to_one_mebi_character_is_sent_and_one_more_refused():
    from openprogram.runtime.conversation.turn_input import MAX_USER_INPUT_CHARS, input_too_large

    assert MAX_USER_INPUT_CHARS == 1_048_576
    assert input_too_large("a" * 1_048_576) is None
    assert "1,048,577" in input_too_large("a" * 1_048_577)


def test_admission_takes_every_message_a_surface_accepts():
    """The 256 KiB envelope budget does not count the person's message: a
    300 KB prompt, under the one-message limit, is admitted and stored; one
    character over that limit is refused as ``input_too_large``."""
    from openprogram.runtime.conversation.production_driver.shared import (
        AGENT_TURN_INPUT_VERSION, AgentDriverError, normalize_agent_turn_payload)
    from openprogram.runtime.conversation.turn_input import MAX_USER_INPUT_CHARS
    from openprogram.runtime.execution.agent_input_budget import AGENT_USER_TEXT_MAX_CHARS
    from openprogram.runtime.execution.store.shared import (
        ExecutionConflict, _validate_agent_turn_payload)

    assert AGENT_USER_TEXT_MAX_CHARS == MAX_USER_INPUT_CHARS

    def payload(text):
        return {"version": AGENT_TURN_INPUT_VERSION, "kind": "chat", "request": {
            "session_id": "s", "user_text": text, "agent_id": "main", "source": "cli"}}

    big = payload("中" * 300_000)
    assert normalize_agent_turn_payload(big)["request"]["user_text"] == "中" * 300_000
    _validate_agent_turn_payload(big)
    over = payload("a" * (MAX_USER_INPUT_CHARS + 1))
    with pytest.raises(AgentDriverError) as refused:
        normalize_agent_turn_payload(over)
    assert refused.value.code == "input_too_large"
    with pytest.raises(ExecutionConflict):
        _validate_agent_turn_payload(over)


def test_print_refuses_a_prompt_longer_than_one_message():
    from openprogram.interfaces.cli.print_input import PromptInputError, resolve_prompt

    assert resolve_prompt("a" * 1_048_576) == "a" * 1_048_576
    with pytest.raises(PromptInputError, match="at most"):
        resolve_prompt("-", io.StringIO("b" * 1_048_577), grace=0)


# --- the card ------------------------------------------------------------------


def test_the_card_shows_the_tools_own_output_and_how_long_it_is():
    from openprogram.runtime.conversation.event_parsing import card_text

    details = {"output": {"full_path": "/x", "total_lines": 900, "total_chars": 50_000},
               "preview": "head of the output"}
    text, full = card_text("head\n[... elided ...]\ntail", details)
    assert text == "head of the output"
    assert full == {"total_lines": 900, "total_chars": 50_000, "shown_chars": 18}
    assert card_text("short", {}) == ("short", None)


def test_view_full_output_reads_the_saved_file(tmp_path):
    from openprogram.interfaces.server.ws_actions.session.tool_output import _whole_output

    saved = tmp_path / "whole.txt"
    saved.write_text("the whole text")
    row = {"content": "bounded", "details": {"output": {"full_path": str(saved)}}}
    assert _whole_output(row) == "the whole text"
    saved.unlink()
    assert _whole_output(row) == "bounded"
