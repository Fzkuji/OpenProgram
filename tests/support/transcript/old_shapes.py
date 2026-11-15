"""Every record shape older conversations keep, written to a real SessionStore.

Each case is a named function that writes one conversation and returns the
facts a client must show for it. ``python -m tests.support.transcript.old_shapes``
regenerates the file the web tests read. Timestamps are fixed so that output
is stable.
"""
from __future__ import annotations

import json
import sys
from typing import Callable

from tests.support.transcript.live_server import SESSION_ID, LiveChat

FIXED_TIME = 1_700_000_000.0

# What each component draws, to tell from a row's markup which one it chose.
ATTACH_CARD = 'class="attach-card"'
SUB_AGENT_STEP = 'data-subagent-heads="child-head"'
RECEIVED_MESSAGE = "agent-event"
USER_BUBBLE = "message user"


class Rows:
    """Appends rows along one branch of the session, oldest first."""

    def __init__(self, chat: LiveChat, session_id: str = SESSION_ID) -> None:
        self.store = chat.sessions
        self.session_id = session_id
        self.last = "ROOT"
        self.clock = FIXED_TIME

    def add(self, row_id: str, role: str, content: str, *, chain: bool = True, **fields) -> str:
        self.clock += 1
        message = {"id": row_id, "role": role, "content": content, "timestamp": self.clock, **fields}
        message.setdefault("predecessor", self.last)
        self.store.append_message(self.session_id, message)
        if chain:
            self.last = row_id
        return row_id

    def turn(self, name: str, reply: str = "reply text", **reply_fields) -> str:
        self.add(name, "user", f"{name} prompt")
        return self.add(f"{name}_reply", "assistant", reply, **reply_fields)

    def finish(self, head: str | None = None) -> None:
        self.store.set_head(self.session_id, head or self.last)


def _attach(**fields) -> dict:
    return {"display": "runtime", "function": "attach", "agent_id": "main", "attach": {
        "session_id": SESSION_ID, "head_id": "child-head", "label": "helper",
        "prompt": "the task", "status": "completed", **fields}}


def _agent_block(prompt: str = "the task", tool_call_id: str = "call-1", tool: str = "agent") -> dict:
    return {"type": "tool", "tool": tool, "tool_call_id": tool_call_id,
            "input": json.dumps({"prompt": prompt}), "result": "ok", "is_error": False}


def _send_block(tool_call_id: str = "call-1") -> dict:
    return {"type": "tool", "tool": "send_message", "tool_call_id": tool_call_id,
            "input": json.dumps({"message": "unrelated", "to": "some-branch"}), "result": "ok", "is_error": False}


def attach_predecessor_only(rows: Rows) -> dict:
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.session_store.shared import _msg_to_node

    owner = rows.turn("a")
    # ``append_message`` copies the predecessor into ``caller`` for attach rows,
    # so the shape with no caller edge has to be written as a node.
    node = _msg_to_node({"id": "attach-1", "role": "assistant", "content": "child result", "timestamp": FIXED_TIME + 9,
                         "predecessor": owner, **_attach()})
    node.caller = ""
    SessionNodeWriter(rows.store, rows.session_id, advance_head=False).append(node)
    rows.finish("attach-1")
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"]], "drawn": [[owner, SUB_AGENT_STEP]]}


def attach_both_edges(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.add("attach-1", "assistant", "child result", caller=owner, **_attach())
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"]], "drawn": [[owner, SUB_AGENT_STEP]]}


def attach_on_user_row(rows: Rows) -> dict:
    user = rows.add("a", "user", "typed /task")
    rows.add("attach-1", "assistant", "child result", **_attach())
    rows.finish()
    return {"rows": [user, "attach-1"], "facts": [["attach-1", "function", "attach"]], "drawn": [["attach-1", SUB_AGENT_STEP]]}


def attach_manual(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.add("attach-1", "assistant", "attached by hand", **_attach(label="manual", prompt="", manual=True))
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.attach.manual", True]],
            "drawn": [[owner, ATTACH_CARD]]}


def attach_cross_session(rows: Rows) -> dict:
    rows.store.create_session("other-session", "main")
    owner = rows.turn("a")
    rows.add("attach-1", "assistant", "from elsewhere", **_attach(session_id="other-session"))
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.attach.session_id", "other-session"]],
            "drawn": [[owner, ATTACH_CARD]]}


def reply_without_blocks(rows: Rows) -> dict:
    owner = rows.turn("a", tool_calls=[{"tool": "lookup", "tool_call_id": "call-1", "input": "{}", "result": "ok"}])
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "tools.0.tool", "lookup"]]}


def row_without_status(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "status", "done"], ["a", "status", "done"]]}


def error_in_content(rows: Rows) -> dict:
    owner = rows.turn("a", reply="provider exploded", status="error")
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "status", "error"], [owner, "errorDetail", "provider exploded"]]}


def row_without_id(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.finish()
    return {"rows": ["a", owner], "facts": [["a", "content", "a prompt"]]}


def extra_as_object(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [{"type": "text", "text": "from object"}]})
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "blocks.0.text", "from object"]]}


def extra_as_json_string(rows: Rows) -> dict:
    owner = rows.turn("a", extra=json.dumps({"blocks": [{"type": "text", "text": "from string"}]}))
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "blocks.0.text", "from string"]]}


def read_speaker(rows: Rows) -> dict:
    rows.add("a", "user", "said in a group", speaker_kind="human", speaker_id="human/ann", speaker_display="Ann")
    rows.finish()
    return {"rows": ["a"], "facts": [["a", "speaker.display", "Ann"]]}


def read_notice(rows: Rows) -> dict:
    rows.add("a", "user", "the notice text", notice=[{"kind": "report", "name": "helper", "line": "Finished"}])
    rows.finish()
    return {"rows": ["a"], "facts": [["a", "notice.0.line", "Finished"]]}


def pasted_elements(rows: Rows) -> dict:
    elements = [{"tail_start": 8, "tail_end": 2, "label": "pasted lines", "head": "long pasted"}]
    rows.add("a", "user", "long pasted text", extra=json.dumps({"text_elements": elements}))
    rows.finish()
    # The store flattens ``extra`` onto the row and the mapper reads only ``extra``.
    return {"rows": ["a"], "facts": [["a", "content", "long pasted text"], ["a", "pastedElements.0.label", "pasted lines"]]}


def chapter_pin(rows: Rows) -> dict:
    rows.add("a", "user", "a pinned chapter", chapter={"pinned_at": FIXED_TIME})
    rows.finish()
    return {"rows": ["a"], "facts": [["a", "chapterPinnedAt", FIXED_TIME]]}


def gui_agent_name(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [
        {"type": "text", "text": "drawn"},
        {"type": "tool", "tool": "gui_agent", "tool_call_id": "call-1", "input": "{}", "result": "ok"},
    ]})
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "blocks.0.text", "drawn"], [owner, "blocks.1.tool", "gui_agent"]],
            "drawn": [[owner, "Ran a GUI call"]]}


def attach_no_call_id_prompt_match(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [_agent_block(prompt="first"), _agent_block(prompt="second", tool_call_id="call-2")]})
    rows.add("attach-1", "assistant", "second result", **_attach(prompt="second"))
    rows.finish()
    # A card with no call id belongs to the agent block holding its prompt.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", "call-2"]],
            "steps": [[owner, ["function", "attach-1"]]]}


def attach_no_call_id_order(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [_agent_block(prompt="first"), _agent_block(prompt="second", tool_call_id="call-2")]})
    rows.add("attach-1", "assistant", "child result", **_attach(prompt="nothing names this"))
    rows.finish()
    # With no prompt match a card belongs to the first agent block.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", "call-1"]],
            "steps": [[owner, ["attach-1", "function"]]]}


def attach_no_call_id_order_skips_send_message(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [_send_block(), _agent_block(prompt="first", tool_call_id="call-2")]})
    rows.add("attach-1", "assistant", "child result", **_attach(prompt="nothing names this"))
    rows.finish()
    # Order pairing counts agent and task blocks, not send_message.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", "call-2"]],
            "steps": [[owner, ["function", "attach-1"]]]}


def attach_task_block(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [_agent_block(prompt="first", tool="task"),
                                             _agent_block(prompt="second", tool_call_id="call-2", tool="task")]})
    rows.add("attach-1", "assistant", "second result", **_attach(prompt="second"))
    rows.finish()
    # A task block pairs by prompt as an agent block does.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", "call-2"]],
            "steps": [[owner, ["function", "attach-1"]]]}


def attach_with_send_message(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [_send_block()]})
    rows.add("attach-1", "assistant", "child result", **_attach(prompt="the task"))
    rows.finish()
    # A send_message block takes no card without evidence.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", None]],
            "steps": [[owner, ["function", "attach-1"]]]}


def attach_unmatched(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [{"type": "tool", "tool": "lookup", "tool_call_id": "call-1", "input": "{}", "result": "ok"}]})
    rows.add("attach-1", "assistant", "child", **_attach(prompt="nothing names this"))
    rows.finish()
    # With no block to take it the card is still drawn, after the steps.
    # A card no block can take is unpaired.
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"], [owner, "attachCards.0.blockId", None]],
            "steps": [[owner, ["function", "attach-1"]]]}


def code_node_without_call_id(rows: Rows) -> dict:
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import ROLE_CODE, Call

    owner = rows.turn("a", extra={"blocks": [{"type": "tool", "tool": "demo", "input": "{}", "result": "ok"}]})
    SessionNodeWriter(rows.store, rows.session_id, advance_head=False).append(Call(
        id="code-1", created_at=FIXED_TIME, role=ROLE_CODE, name="demo", input={}, output="ok", caller=owner, metadata={}))
    rows.finish(owner)
    # The caller edge folds the node into its reply, on the block already there.
    return {"rows": ["a", owner], "facts": [[owner, "tools.0.tool", "demo"], [owner, "tools.length", 1], [owner, "blocks.length", 1]]}


def receipt_without_origin(rows: Rows) -> dict:
    rows.store.create_session("sender-session", "main")
    rows.add("a", "user", "[message from sender-session (helper)]\nthe delivered body")
    rows.add("b", "user", "[message from no-such-session]\na typed line")
    rows.finish()
    return {"rows": ["a", "b"], "facts": [["a", "content", "[message from sender-session (helper)]\nthe delivered body"],
                                          ["b", "content", "[message from no-such-session]\na typed line"]],
            # F36: a typed line naming no real session stays the person's own message.
            "drawn": [["a", RECEIVED_MESSAGE], ["a", "Message from \u201chelper\u201d"], ["b", USER_BUBBLE]]}


def old_steer_row(rows: Rows) -> dict:
    # Old steers sit between the prompt and the reply they redirected.
    rows.add("a", "user", "a prompt")
    rows.add("steer-1", "user", "change course", steering=True)
    owner = rows.add("a_reply", "assistant", "reply text")
    rows.finish()
    return {"rows": ["a", "steer-1", owner], "facts": [["steer-1", "steeringReplyId", owner], [owner, "blocks.0.message_id", "steer-1"]]}


def steering_already_in_blocks(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [
        {"type": "text", "text": "before"},
        {"type": "steering", "message_id": "steer-1", "text": "change course", "timestamp": FIXED_TIME},
    ]})
    rows.add("steer-1", "user", "change course", steering=True)
    rows.finish()
    return {"rows": ["a", owner, "steer-1"], "facts": [["steer-1", "steeringReplyId", owner], [owner, "blocks.1.message_id", "steer-1"], [owner, "blocks.length", 2]]}


def restart_prompt(rows: Rows) -> dict:
    cut = rows.turn("a", reply="cut off")
    rows.add("restart-1", "user", "continue after the restart", trigger="restart")
    next_reply = rows.add("restart-1_reply", "assistant", "carried on", predecessor="restart-1")
    rows.finish()
    return {"rows": ["a", cut, "restart-1", next_reply], "facts": [["restart-1", "trigger", "restart"], [cut, "continuedBy", next_reply]]}


def fallback_blocks(rows: Rows) -> dict:
    owner = rows.turn("a", tool_calls=[{"tool": "lookup", "tool_call_id": "call-1", "input": "{}", "result": "ok", "is_error": True}])
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "tools.0.status", "error"], [owner, "content", "reply text"]]}


def summary_covers_ids(rows: Rows) -> dict:
    rows.turn("a")
    # A summary replaces rows for the model, shares the first covered row's
    # predecessor, and can never be the branch tip.
    rows.add("summary-1", "system", "a summary", chain=False, kind="compaction", slot="card", predecessor="ROOT",
             covers_ids=["a", "a_reply"], summarised_count=2)
    rows.turn("b")
    rows.finish()
    return {"rows": ["a", "a_reply", "summary-1_card", "summary-1_ui", "b", "b_reply"],
            "order": ["a", "a_reply", "summary-1_card", "summary-1_ui", "b", "b_reply"],
            "facts": [["summary-1_card", "kind", "compaction"], ["summary-1_card", "slot", "card"],
                      ["a", "coveredBy", "summary-1_card"], ["a_reply", "coveredBy", "summary-1_card"],
                      ["summary-1_ui", "kind", "compaction"], ["summary-1_ui", "slot", "event"],
                      ["b_reply", "content", "reply text"], ["a", "siblingTotal", 1]],
            "drawn": [["summary-1_card", "compaction-card"]]}


def old_tool_row_by_provider_id(rows: Rows) -> dict:
    owner = rows.turn("a", extra={"blocks": [{"type": "tool", "tool": "lookup", "tool_call_id": "call-9", "input": "{}", "result": "short"}]})
    rows.add("call-9", "tool", "the whole output", function="lookup", caller=owner, chain=False, predecessor=owner)
    rows.finish(owner)
    return {"rows": ["a", owner], "facts": [[owner, "tools.0.id", "call-9"]]}


def head_folded_by_aggregation(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.add("tool-1", "tool", "output", function="lookup", caller=owner, predecessor=owner, tool_call_id="call-1")
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "content", "reply text"]]}


def broken_predecessor_chain(rows: Rows) -> dict:
    rows.add("a", "user", "older prompt")
    rows.add("b", "user", "newer prompt", predecessor="row-that-is-gone")
    rows.finish()
    return {"rows": ["b"], "facts": [["b", "content", "newer prompt"]]}


def running_attach_without_job(rows: Rows) -> dict:
    owner = rows.turn("a")
    rows.add("attach-1", "assistant", "(running)", status="running", **_attach(status="running"))
    rows.finish()
    return {"rows": ["a", owner], "facts": [[owner, "attachCards.0.id", "attach-1"]]}


OLD_SHAPES: dict[str, Callable[[Rows], dict]] = {
    case.__name__: case for case in (
        attach_predecessor_only, attach_both_edges, attach_on_user_row, attach_manual, attach_cross_session,
        reply_without_blocks, row_without_status, error_in_content, row_without_id, extra_as_object,
        extra_as_json_string, read_speaker, read_notice, pasted_elements, chapter_pin, gui_agent_name,
        attach_no_call_id_prompt_match, attach_no_call_id_order, attach_no_call_id_order_skips_send_message,
        attach_task_block, attach_with_send_message, attach_unmatched, code_node_without_call_id,
        receipt_without_origin, old_steer_row, steering_already_in_blocks, restart_prompt, fallback_blocks,
        summary_covers_ids, old_tool_row_by_provider_id, head_folded_by_aggregation, broken_predecessor_chain,
        running_attach_without_job,
    )
}


def build_case(name: str, chat: LiveChat) -> dict:
    """Write one case and read it back cold, through the real server path."""
    expected = OLD_SHAPES[name](Rows(chat))
    return {"name": name, "frames": chat.whole_read(), **expected}


def main() -> None:  # pragma: no cover - a command for people
    import os
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="openprogram-old-shapes-") as home:
        os.environ["HOME"] = home
        from tests.support.transcript.live_server import live_chat
        from tests.support.transcript.record_chats import RECORDINGS

        cases = []
        for name in OLD_SHAPES:
            root = Path(home) / name
            root.mkdir()
            with live_chat(root) as chat:
                cases.append(build_case(name, chat))
        target = RECORDINGS.parent / "old-shapes.json"
        target.write_text(dump(cases), encoding="utf-8")
        sys.stdout.write(f"{len(cases)} cases, {target.stat().st_size} bytes\n")


def dump(cases: list[dict]) -> str:
    return "[\n" + ",\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True, default=str) for case in cases) + "\n]\n"


if __name__ == "__main__":
    main()
