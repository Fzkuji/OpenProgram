"""The conversations a recording captures, each on a real :class:`LiveChat`.

Run ``python -m tests.support.transcript.record_chats`` after a change to what
the server emits. The web runner replays the files this produces.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from tests.component.providers.scripted_provider import (
    ScriptedError,
    ScriptedText,
    ScriptedThinking,
    ScriptedToolCall,
)
from tests.support.transcript.live_server import LiveChat, wait_until


def fresh_chat(chat: LiveChat) -> str:
    chat.model.add(ScriptedThinking("planning"), ScriptedText("the answer"))
    chat.send("hello")
    chat.wait_finished()
    return "A first message streams its reply to completion."


def tool_turn(chat: LiveChat) -> str:
    chat.model.add(ScriptedText("before"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("after"))
    chat.send("use a tool")
    chat.wait_finished()
    return "Text, a tool call and more text keep their order."


def second_turn(chat: LiveChat) -> str:
    chat.model.add(ScriptedText("first answer"))
    chat.model.add(ScriptedText("second answer"))
    chat.send("one")
    chat.wait_finished()
    chat.send("two")
    chat.wait_finished()
    return "A later turn continues the same branch."


def provider_error(chat: LiveChat) -> str:
    chat.model.add(ScriptedText("partial"), ScriptedError("provider exploded", retryable=False))
    chat.send("this fails")
    chat.wait_finished()
    return "A provider failure settles the reply with its partial text."


def stopped_during_tool(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    chat.model.add(ScriptedText("already generated"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.send("stop me")
    chat.wait_tool("lookup")
    chat.cancel()
    chat.wait_finished()
    return "Stop during a tool call keeps the partial text and ends the run."


def stopped_before_start(chat: LiveChat) -> str:
    from openprogram.runtime.conversation.production_driver.shared import CanonicalAgentEntry

    activate = CanonicalAgentEntry.activate
    waiting, start = threading.Event(), threading.Event()

    async def held(entry, admission):
        waiting.set()
        await asyncio.to_thread(start.wait, 30)
        return await activate(entry, admission)

    CanonicalAgentEntry.activate = held
    try:
        chat.send("stop me before you start")
        wait_until(waiting.is_set, what="the turn waiting to start")
        chat.cancel()
        start.set()
        chat.wait_finished()
    finally:
        start.set()
        CanonicalAgentEntry.activate = activate
    return "Stop before the turn starts ends it as a stopped reply."


def read_during_stream(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    chat.model.add(ScriptedText("before"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("after"))
    chat.send("read me mid-turn")
    chat.wait_tool("lookup")
    chat.checkpoint("while the tool runs")
    chat.client_read("mid-turn")
    chat.checkpoint("after the mid-turn read")
    chat.release_tool("lookup")
    chat.wait_finished()
    return "A transcript read during a run lands between stream frames."


def reconnect_during_stream(chat: LiveChat) -> str:
    chat.tools.held.update({"lookup", "build"})
    chat.model.add(ScriptedText("first"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("second"), ScriptedToolCall("build", {}, "call-2"))
    chat.model.add(ScriptedText("third"))
    chat.send("drop the socket")
    chat.wait_tool("lookup")
    chat.disconnect()
    chat.release_tool("lookup")
    chat.wait_tool("build")
    chat.reconnect()
    chat.release_tool("build")
    chat.wait_finished()
    return "Frames lost while the socket is down are recovered by the reconnect read."


def steered_turn(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    chat.model.add(ScriptedText("before"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("after the steer"))
    chat.send("start")
    chat.wait_tool("lookup")
    chat.steer("change course")
    chat.release_tool("lookup")
    chat.wait_finished()
    return "A steer during a tool call joins the reply it redirected."


def _sub_agent(chat: LiveChat, **tool_input) -> None:
    from openprogram.programs import agent_tools

    chat.tools.extra.extend(agent_tools(["agent"]))
    chat.model.add(ScriptedText("spawning"), ScriptedToolCall(
        "agent", {"prompt": "do the sub task", "description": "sub task", **tool_input}, "call-agent"))


def sync_sub_agent(chat: LiveChat) -> str:
    _sub_agent(chat)
    chat.model.add(ScriptedText("sub agent reply"))
    chat.model.add(ScriptedText("done with the sub agent"))
    chat.send("delegate this")
    chat.wait_finished()
    chat.wait_published()
    return "A synchronous sub-agent spawned by the agent tool."


def background_sub_agent(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    _sub_agent(chat, run_in_background=True)
    chat.model.add(ScriptedText("the sub agent runs in the background"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("finished"))
    chat.model.by_prompt["do the sub task"] = (ScriptedText("sub agent reply"),)
    chat.send("delegate in the background")
    chat.wait_tool("lookup")
    # The sub-agent's one scripted reply ends it while the turn's tool is
    # held: wait for that end, or the card's state here is a race.
    chat.wait_job_finished()
    chat.checkpoint("the background sub-agent has started")
    chat.release_tool("lookup")
    chat.wait_finished()
    chat.wait_job_finished()
    return "A background sub-agent's card is written when it starts, while the turn goes on."


def slash_spawn_action(chat: LiveChat) -> str:
    chat.model.add(ScriptedText("spawned reply"))
    chat.send("/task helper: do the slash task")
    chat.wait_finished()
    chat.wait_published()
    return "The spawn action path: its result frame names the user's row."


def peer_turn(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    chat.model.add(ScriptedText("before"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("after"))
    chat.send("from another tab", peer=True)
    chat.wait_tool("lookup")
    chat.release_tool("lookup")
    chat.wait_finished()
    return "A turn sent from another tab reaches this one with no ack."


def provider_citations(chat: LiveChat) -> str:
    chat.tools.held.add("lookup")
    chat.model.add(ScriptedText("cited text", citations=[
        {"type": "url_citation", "url": "https://example.invalid/source", "title": "Source", "cited_text": "cited"},
    ]), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("finished"))
    chat.send("search for it")
    chat.wait_tool("lookup")
    chat.checkpoint("the cited text is finished and the turn is running")
    chat.release_tool("lookup")
    chat.wait_finished()
    return "Citations the provider attaches to a text block."


def _write_function_node(chat: LiveChat, *, caller: str, node_id: str, status: str):
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call, ROLE_CODE

    writer = SessionNodeWriter(chat.sessions, chat.session_id, advance_head=False)
    if status == "running":
        writer.append(Call(id=node_id, created_at=time.time(), role=ROLE_CODE, name="demo_function",
                           input={}, output="", caller=caller, metadata={"status": "running"}))
    else:
        writer.update(node_id, output="demo result", metadata={"status": status})


def _tree_updates(chat: LiveChat) -> int:
    return chat.tree_updates


def function_run_tree(chat: LiveChat) -> str:
    from openprogram.runtime.execution.projections.dag import live_progress

    chat.model.add(ScriptedText("before the function run"))
    chat.send("hello")
    chat.wait_finished()
    with live_progress(chat.session_id, "", "demo_function"):
        _write_function_node(chat, caller="", node_id="function-run-node", status="running")
        wait_until(lambda: _tree_updates(chat) >= 1, what="a tree update")
        _write_function_node(chat, caller="", node_id="function-run-node", status="success")
        wait_until(lambda: _tree_updates(chat) >= 2, what="the second tree update")
    wait_until(lambda: _tree_updates(chat) >= 3, what="the closing tree update")
    chat.wait_published()
    return "A function run whose progress frames reach a client with no row for it."


def function_tree_in_reply(chat: LiveChat) -> str:
    from openprogram.runtime.execution.projections.dag import live_progress

    reply: dict[str, str] = {}

    def during() -> None:
        reply["id"] = f"{_latest_user(chat)}_reply"
        with live_progress(chat.session_id, reply["id"], "demo_function"):
            _write_function_node(chat, caller=reply["id"], node_id="model-function-node", status="running")
            wait_until(lambda: _tree_updates(chat) >= 1, what="a tree update")
            _write_function_node(chat, caller="model-function-node", node_id="model-function-step", status="running")
            _write_function_node(chat, caller="model-function-node", node_id="model-function-step", status="success")
            _write_function_node(chat, caller=reply["id"], node_id="model-function-node", status="success")

    chat.tools.during["lookup"] = during
    chat.model.add(ScriptedText("calling a function"), ScriptedToolCall("lookup", {}, "call-1"))
    chat.model.add(ScriptedText("function finished"))
    chat.send("call the function")
    chat.wait_finished()
    with live_progress(chat.session_id, reply["id"], "demo_function"):
        pass
    return "A model-issued function's call tree, then one more tree update after the turn ended."


def _latest_user(chat: LiveChat) -> str:
    frames = [s["frame"]["data"] for s in chat.timeline
              if s.get("frame", {}).get("type") == "chat_ack"]
    return frames[-1]["msg_id"]


def page_edge_attach(chat: LiveChat) -> str:
    before = chat.filler(48, prefix="before")
    _sub_agent(chat)
    chat.model.add(ScriptedText("sub agent reply"))
    chat.model.add(ScriptedText("done with the sub agent"))
    chat.send("delegate this")
    chat.wait_finished()
    chat.wait_published()
    # The first page holds 6 turns, so the sub-agent's turn is its oldest.
    chat.filler(5)
    chat.reopen()
    chat.checkpoint("first page starts at the sub-agent turn", partial=True)
    chat.page("older")
    chat.checkpoint("an older page ends before the sub-agent turn", partial=True)
    # Positions: 48 filler turns, then the sub-agent's turn at 48. A window of
    # 25 around 35 covers 23 to 47, so the turn comes whole on the newer page.
    chat.reopen()
    chat.page("around", around=before[35])
    chat.page("newer")
    chat.checkpoint("the sub-agent turn arrived on the newer page", partial=True)
    return "A sub-agent's turn sits at the edge of a history page."


def page_edge_steer(chat: LiveChat) -> str:
    chat.filler(3, prefix="before")
    steered_turn(chat)
    chat.filler(5)
    chat.reopen()
    chat.checkpoint("first page starts at the steered turn", partial=True)
    chat.page("older")
    chat.checkpoint("the older page brought the turns before it", partial=True)
    return "A steered turn sits at the edge of a history page."


def page_edge_restart(chat: LiveChat) -> str:
    from tests.support.transcript.old_shapes import Rows

    before = chat.filler(48, prefix="before")
    rows = Rows(chat)
    rows.last = before[-1]
    rows.turn("cut", reply="cut off")
    rows.add("restart-1", "user", "continue after the restart", trigger="restart")
    rows.add("restart-1_reply", "assistant", "carried on")
    rows.finish()
    # The first page holds 6 turns, so the restarted turn is its oldest.
    chat.filler(5)
    chat.reopen()
    chat.checkpoint("first page starts at the restarted turn", partial=True)
    chat.page("older")
    chat.checkpoint("an older page ends before the restarted turn", partial=True)
    # Positions: 48 filler turns, then the restarted turn at 48. A window of
    # 25 around 35 covers 23 to 47, so the turn comes whole on the newer page.
    chat.reopen()
    chat.page("around", around=before[35])
    chat.checkpoint("window ends before the restarted turn", partial=True)
    chat.page("newer")
    chat.checkpoint("the restarted turn arrived on the newer page", partial=True)
    return "A reply cut by a restart and its continuation sit at the edge of a history page."


SCENARIOS: dict[str, Callable[[LiveChat], str]] = {
    "fresh_chat": fresh_chat,
    "tool_turn": tool_turn,
    "second_turn": second_turn,
    "provider_error": provider_error,
    "stopped_during_tool": stopped_during_tool,
    "stopped_before_start": stopped_before_start,
    "read_during_stream": read_during_stream,
    "reconnect_during_stream": reconnect_during_stream,
    "steered_turn": steered_turn,
    "sync_sub_agent": sync_sub_agent,
    "slash_spawn_action": slash_spawn_action,
    "page_edge_attach": page_edge_attach,
    "page_edge_steer": page_edge_steer,
    "page_edge_restart": page_edge_restart,
    "peer_turn": peer_turn,
    "provider_citations": provider_citations,
    "function_run_tree": function_run_tree,
    "function_tree_in_reply": function_tree_in_reply,
    "background_sub_agent": background_sub_agent,
}
