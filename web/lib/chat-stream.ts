/**
 * Chat-stream WS reducer.
 *
 * Translates the backend's chat WebSocket protocol into session-store
 * mutations — the data half of the React message-stream port. Pure:
 * call `applyChatWsMessage(msg)` with a parsed `{ type, data }`
 * envelope and it updates `messagesById` / `messageOrder`.
 *
 * NOT attached to a socket here. Phase 3 (cutover) wires it onto the
 * shared `window.ws` and removes the legacy `chat-ws.js` renderer.
 * Until then this module is dormant — building it is additive and
 * leaves the live (legacy) chat untouched.
 *
 * Protocol (mirrors `public/js/chat/chat-ws.js`):
 *   chat_ack       { session_id, msg_id }
 *       → the user turn registered; create the assistant reply
 *         placeholder so streaming deltas have somewhere to land.
 *   chat_response  { type, msg_id, session_id, ... }
 *       type === "stream_event"  { event: { type, ... } }
 *           text        → append to reply.content
 *           thinking    → append to reply.thinking
 *           tool_use    → push a ChatToolCall (status "running")
 *           tool_result → fill the matching tool's result + status
 *       type === "result" | "error" | "cancelled"
 *           → finalize the reply (status + any final text)
 *   other chat_response types (status / tree_update / context_stats /
 *   user_message / follow_up_question) are NOT message-stream concerns
 *   and are left to their own handlers.
 */

import { useSessionStore, type ChatMsg, type ChatToolCall } from "./session-store";

interface StreamEvent {
  type: "text" | "thinking" | "tool_use" | "tool_result";
  text?: string;
  tool?: string;
  input?: string;
  tool_call_id?: string;
  result?: string;
  is_error?: boolean;
  elapsed?: number;
}

interface ChatResponseData {
  type: string;
  msg_id?: string;
  session_id?: string;
  function?: string;
  display?: "runtime" | "normal";
  event?: StreamEvent;
  content?: string;
  text?: string;
  cancelled?: boolean;
}

interface WsEnvelope {
  type: string;
  data?: unknown;
}

/** Store key for an assistant turn's reply bubble. The user turn is
 *  keyed by its bare `msg_id`; the reply gets a `_reply` suffix so the
 *  two never collide in `messagesById`. */
function replyId(msgId: string): string {
  return `${msgId}_reply`;
}

export function applyChatWsMessage(msg: WsEnvelope): void {
  if (msg.type === "chat_ack") {
    handleAck(msg.data as { session_id?: string; msg_id?: string });
    return;
  }
  if (msg.type === "chat_response") {
    handleResponse(msg.data as ChatResponseData);
  }
}

function handleAck(d: { session_id?: string; msg_id?: string } | undefined): void {
  if (!d?.session_id || !d?.msg_id) return;
  const store = useSessionStore.getState();
  const rid = replyId(d.msg_id);
  if (store.messagesById[rid]) return;
  store.appendMessage(d.session_id, {
    id: rid,
    role: "assistant",
    content: "",
    status: "streaming",
  });
}

function handleResponse(d: ChatResponseData | undefined): void {
  if (!d || !d.session_id || !d.msg_id) return;

  // A user turn — either echoed back by the server or broadcast from a
  // peer. Keyed by the bare `msg_id` (the reply takes the `_reply`
  // suffix), so it never collides with its own assistant bubble.
  if (d.type === "user_message") {
    handleUserMessage(d);
    return;
  }

  const rid = replyId(d.msg_id);

  if (d.type === "stream_event" && d.event) {
    applyStreamEvent(d.session_id, rid, d.event);
    return;
  }
  if (d.type === "result" || d.type === "error" || d.type === "cancelled") {
    finalize(d.session_id, rid, d);
  }
}

function handleUserMessage(d: ChatResponseData): void {
  if (!d.session_id || !d.msg_id) return;
  const store = useSessionStore.getState();
  if (store.messagesById[d.msg_id]) return;
  store.appendMessage(d.session_id, {
    id: d.msg_id,
    role: "user",
    content: d.content ?? d.text ?? "",
    display: d.display === "runtime" ? "runtime" : undefined,
    status: "done",
  });
}

/**
 * Optimistically add the just-sent user turn to the store so the
 * bubble appears immediately — before the server echoes it back.
 * The composer's send path calls this; the later `user_message` /
 * `chat_ack` for the same id is de-duped by id.
 */
export function appendLocalUserTurn(
  sessionId: string,
  msgId: string,
  text: string,
  display?: "runtime" | "normal",
): void {
  const store = useSessionStore.getState();
  if (store.messagesById[msgId]) return;
  store.appendMessage(sessionId, {
    id: msgId,
    role: "user",
    content: text,
    display: display === "runtime" ? "runtime" : undefined,
    status: "done",
  });
}

function applyStreamEvent(sid: string, rid: string, evt: StreamEvent): void {
  const store = useSessionStore.getState();
  const cur = store.messagesById[rid];
  if (!cur) return; // placeholder must exist — chat_ack creates it

  switch (evt.type) {
    case "text":
      store.updateMessage(sid, rid, {
        content: cur.content + (evt.text ?? ""),
        status: "streaming",
      });
      break;
    case "thinking":
      store.updateMessage(sid, rid, {
        thinking: (cur.thinking ?? "") + (evt.text ?? ""),
        status: "streaming",
      });
      break;
    case "tool_use": {
      const tools: ChatToolCall[] = [...(cur.tools ?? [])];
      tools.push({
        id: evt.tool_call_id || `t_${Date.now()}_${tools.length}`,
        tool: evt.tool || "?",
        input: evt.input ?? "",
        status: "running",
      });
      store.updateMessage(sid, rid, { tools, status: "streaming" });
      break;
    }
    case "tool_result": {
      const tools = (cur.tools ?? []).map((t): ChatToolCall =>
        t.id === evt.tool_call_id
          ? {
              ...t,
              result: evt.result ?? "",
              isError: !!evt.is_error,
              status: evt.is_error ? "error" : "done",
            }
          : t,
      );
      store.updateMessage(sid, rid, { tools });
      break;
    }
  }
}

function finalize(sid: string, rid: string, d: ChatResponseData): void {
  const store = useSessionStore.getState();
  const cur = store.messagesById[rid];
  if (!cur) return;

  const status: ChatMsg["status"] =
    d.type === "error"
      ? "error"
      : d.type === "cancelled" || d.cancelled
        ? "cancelled"
        : "done";

  const patch: Partial<ChatMsg> = { status };
  if (d.function) patch.function = d.function;
  if (d.display) patch.display = d.display;

  // `result` carries the full final text. Streaming usually already
  // built `content` delta-by-delta; only fall back to the result's
  // text when nothing streamed (e.g. a non-streaming run).
  const finalText = d.content ?? d.text;
  if (finalText && !cur.content) patch.content = finalText;

  // Any tool still "running" at terminal time gets closed out — no
  // tool_result will arrive after the turn ends.
  if (cur.tools?.some((t) => t.status === "running")) {
    patch.tools = cur.tools.map((t): ChatToolCall =>
      t.status === "running" ? { ...t, status: "done" } : t,
    );
  }

  store.updateMessage(sid, rid, patch);
}
