// One turn as the server publishes it, delivered to the browser the way the
// product delivers it: a first read, numbered transcript updates for rows and
// blocks, and text frames placed by offset. Browser tests drive this instead of
// raw stream events, which write no rows.
import { applyFirstRead, applyTranscriptUpdate } from "../../lib/runtime-bridge/session/transcript-updates.ts";
import { applyChatWsMessage } from "../../lib/net/ws/chat-stream.ts";
import { flushTextFrames } from "../../lib/net/ws/text-frames.ts";

/** A session view holding one turn opened by `userRow`. */
export function publishedTurn(sessionId, userRow, { revision = 1 } = {}) {
  let cursor = revision;
  let turnId = userRow.id;
  applyFirstRead(sessionId, {
    turns: [{ id: turnId, rows: [userRow] }],
    window: { head_id: turnId, revision: cursor, before: null, after: null, start: 0, total: 1 },
  });

  function update(changes) {
    applyTranscriptUpdate({ session_id: sessionId, view: sessionId, previous: cursor, revision: cursor + 1, changes });
    cursor += 1;
  }

  /** A server row of the current turn, placed after `afterRowId` when new. */
  function putRow(row, afterRowId = null) {
    update([{ kind: "row_upsert", turnId, afterRowId, row: structuredClone(row) }]);
  }

  /** A chat_response frame of the current turn. */
  function frame(data) {
    applyChatWsMessage({ type: "chat_response", data: { session_id: sessionId, msg_id: turnId, ...data } });
  }

  let reply;
  let replyAfter;
  function startReply(fields = {}, afterRowId = turnId) {
    reply = { id: `${turnId}_reply`, role: "assistant", rowKind: "assistant", content: "", status: "running", blocks: [], tools: [], ...fields };
    replyAfter = afterRowId;
    publishReply();
  }
  function publishReply(fields = {}) {
    reply = { ...reply, ...fields };
    putRow(reply, replyAfter);
  }

  /** Text and thinking stream as frames by offset; the next row update carries them. */
  function stream(type, text) {
    const blocks = reply.blocks;
    const last = blocks[blocks.length - 1];
    let block = last?.type === type ? last : undefined;
    if (!block) {
      block = { type, text: "", id: `${type}:${blocks.filter((item) => item.type === type).length}` };
      blocks.push(block);
    }
    frame({ type: "stream_event", event: { type, text }, row_id: reply.id, item_id: block.id, item_index: blocks.indexOf(block), offset: block.text.length });
    flushTextFrames();
    block.text += text;
    if (type === "text") reply.content += text;
  }

  /** What the server's row says after this model event. */
  function event(e) {
    if (e.type === "text" || e.type === "thinking") return stream(e.type, e.text);
    if (e.type === "tool_use") {
      reply.blocks.push({ type: "tool", tool: e.tool, tool_call_id: e.tool_call_id, input: e.input ?? "", id: e.tool_call_id });
      if (!e.agentic) reply.tools.push({ id: e.tool_call_id, tool: e.tool, input: e.input ?? "", status: "running" });
    } else if (e.type === "tool_result") {
      reply.blocks = reply.blocks.map((block) => block.tool_call_id === e.tool_call_id
        ? { ...block, result: e.result ?? "", is_error: !!e.is_error } : block);
      reply.tools = reply.tools.map((tool) => tool.id === e.tool_call_id
        ? { ...tool, result: e.result ?? "", isError: !!e.is_error, status: e.is_error ? "error" : "done" } : tool);
    } else if (e.type === "steer") {
      // The steer is its own user row before the reply, and a block in it.
      const steer = { id: e.id, role: "user", rowKind: "user", content: e.text, status: "done", steering: true, steeringReplyId: reply.id, timestamp: e.timestamp };
      putRow(steer, turnId);
      replyAfter = e.id;
      reply.blocks.push({ type: "steering", message_id: e.id, text: e.text, timestamp: e.timestamp, id: `steering:${e.id}` });
    } else {
      frame({ type: "stream_event", event: e, row_id: reply.id });
      return;
    }
    publishReply();
  }

  /** Function call trees on the reply, keyed by path. */
  function callRoot(root) {
    const roots = (reply.callRoots ?? []).filter((item) => item.path !== root.path);
    publishReply({ callRoots: [...roots, root] });
  }

  /** A new turn after this one; later calls address it. */
  function nextTurn(row) {
    const previous = turnId;
    turnId = row.id;
    update([{ kind: "turn_upsert", turn: { id: turnId, rows: [row] }, afterTurnId: previous }]);
  }

  return {
    update, putRow, frame, startReply, publishReply, event, callRoot, nextTurn,
    get reply() { return reply; },
  };
}
