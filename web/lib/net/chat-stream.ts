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

import { useSessionStore, type ChatMsg, type ChatToolCall } from "@/lib/session-store";

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
  context_tree?: unknown;
  /** Live execution tree carried by `tree_update` envelopes. */
  tree?: unknown;
  usage?: unknown;
  attempts?: { content: string; timestamp: number; tree?: unknown; usage?: unknown }[];
  current_attempt?: number;
  /** parent_id for runtime-block placeholder rows written by the
   *  dispatcher's @agentic_function wrapper — anchors the row to the
   *  assistant reply that called the tool. */
  parent_id?: string;
  status?: string;
  /** Structured error taxonomy on a `type:"error"` response — lets the
   *  bubble show a categorized, actionable error (rate-limit retry hint vs
   *  fatal auth/context) instead of an opaque string. See
   *  docs/design/providers/error-taxonomy-propagation.md. */
  reason?: string;
  retryable?: boolean;
  retry_after_s?: number;
}

/** Names of LLM-callable @agentic_function tools. When the LLM invokes
 *  one of these we DON'T want it to show up under the assistant bubble
 *  as a folded chat-tool card — the dispatcher writes a separate
 *  ``display=runtime`` placeholder row for it which renders as a
 *  full RuntimeBlock + ExecutionDAG. Keeping a parallel folded row
 *  would just duplicate the call. */
const AGENTIC_TOOL_NAMES: ReadonlySet<string> = new Set([
  "gui_agent",
  "research_agent",
  "wiki_agent",
]);

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

/** A `chat_ack` only tells us which conversation the turn belongs to —
 *  for a brand-new chat that's the first time the server-assigned id is
 *  known. The assistant reply bubble is NOT created here: doing so
 *  would land it in `messageOrder` before the user turn (whose
 *  `user_message` broadcast can arrive either side of the ack). The
 *  reply is created lazily on the first stream event / result instead,
 *  by which point the user turn is already in place. */
function handleAck(d: { session_id?: string; msg_id?: string } | undefined): void {
  if (!d?.session_id) return;
  useSessionStore.getState().setCurrentConv(d.session_id);

  // The server does NOT echo a web-originated user turn back as a
  // `user_message` broadcast (only channel/peer turns get that). So the
  // user bubble is created here, from the text the composer stashed on
  // `window.__pendingUserText` just before sending. `chat_ack.msg_id`
  // IS the user turn's id — keying it here lets the reply (`_reply`
  // suffix) and the later result anchor to the same turn.
  const w = window as unknown as { __pendingUserText?: string };
  const text = w.__pendingUserText;
  if (d.msg_id && typeof text === "string" && text) {
    w.__pendingUserText = undefined;
    const isRun = /^(run|create|fix)\s/i.test(text);
    appendLocalUserTurn(
      d.session_id,
      d.msg_id,
      text,
      isRun ? "runtime" : undefined,
    );
    // Create the reply bubble right away (after the user turn, so the
    // order is right) — gives an immediate typing indicator / pending
    // runtime block instead of a gap until the first stream event.
    const rid = replyId(d.msg_id);
    ensureReply(d.session_id, rid);
    if (isRun) {
      useSessionStore
        .getState()
        .updateMessage(d.session_id, rid, { display: "runtime" });
    }
  }
}

/** Fetch the assistant reply bubble, creating it on first use. Keeps
 *  reply creation after the user turn in `messageOrder`. */
function ensureReply(sid: string, rid: string): ChatMsg {
  const store = useSessionStore.getState();
  const existing = store.messagesById[rid];
  if (existing) return existing;
  store.appendMessage(sid, {
    id: rid,
    role: "assistant",
    content: "",
    status: "streaming",
  });
  return useSessionStore.getState().messagesById[rid];
}

function handleResponse(d: ChatResponseData | undefined): void {
  if (!d || !d.msg_id) return;
  // Stream / result envelopes don't always carry `session_id` — the
  // legacy renderer only ever keyed off `msg_id`. Fall back to the
  // store's current conversation (set by the preceding `chat_ack`).
  const sid =
    d.session_id || useSessionStore.getState().currentSessionId || undefined;
  if (!sid) return;

  // A user turn — either echoed back by the server or broadcast from a
  // peer. Keyed by the bare `msg_id` (the reply takes the `_reply`
  // suffix), so it never collides with its own assistant bubble.
  if (d.type === "user_message") {
    handleUserMessage(sid, d);
    return;
  }

  // Runtime-block placeholder rows are written by the dispatcher
  // (``_wrap_agentic_runtime_block``) as separate ChatMsgs with their
  // own server-assigned id (NOT the ``_reply`` suffix). They carry the
  // execution DAG for an LLM-issued @agentic_function call and render
  // as a standalone RuntimeBlock. Detect by ``display=runtime`` and
  // route by the raw msg_id, so we mutate the right row (not the
  // owning assistant reply).
  if (d.display === "runtime" && (d.type === "status" || d.type === "result")) {
    handleRuntimeRow(sid, d);
    return;
  }

  const rid = replyId(d.msg_id);

  // Live execution tree for a streaming `/run` — store it on the reply
  // so <RuntimeBlock />'s <ExecutionTree /> renders it as it grows.
  if (d.type === "tree_update" && d.tree) {
    // LLM-issued @agentic_function path: the dispatcher anchors
    // live_progress on the runtime-block id (not a `_reply` row), so a
    // tree_update arrives with msg_id == runtime_id. If that row exists
    // — either as a top-level ChatMsg or nested inside a parent
    // assistant's runtimeChildren — update IT in place instead of
    // creating a phantom `<runtime_id>_reply` placeholder.
    const store0 = useSessionStore.getState();
    const existingRuntime = d.msg_id ? store0.messagesById[d.msg_id] : undefined;
    if (existingRuntime && existingRuntime.display === "runtime") {
      store0.updateMessage(sid, d.msg_id!, {
        function: d.function ?? existingRuntime.function,
        contextTree: d.tree as never,
      });
      return;
    }
    // Search inside runtimeChildren of any assistant message.
    if (d.msg_id) {
      let foundParent: string | null = null;
      for (const [pid, m] of Object.entries(store0.messagesById)) {
        const kids = m.runtimeChildren;
        if (!kids) continue;
        if (kids.some((c) => c.id === d.msg_id)) {
          foundParent = pid;
          break;
        }
      }
      if (foundParent) {
        const parent = store0.messagesById[foundParent];
        const list = parent?.runtimeChildren ?? [];
        const next = list.map((c) =>
          c.id === d.msg_id
            ? { ...c, function: d.function ?? c.function, contextTree: d.tree as never }
            : c,
        );
        store0.updateMessage(sid, foundParent, { runtimeChildren: next });
        return;
      }
    }
    ensureReply(sid, rid);
    useSessionStore.getState().updateMessage(sid, rid, {
      display: "runtime",
      function: d.function,
      contextTree: d.tree as never,
    });
    return;
  }

  if (d.type === "stream_event" && d.event) {
    // A `/run` turn: tag the reply as a runtime turn up front so
    // <MessageList /> routes it to <RuntimeBlock />, which renders the
    // `#runtime_pending` host the legacy CLI/tree stream handlers
    // target. `_chat` / `chat` are plain chat — left as assistant.
    const isRuntime =
      d.display === "runtime" ||
      (!!d.function && d.function !== "_chat" && d.function !== "chat");
    if (isRuntime) {
      ensureReply(sid, rid);
      useSessionStore.getState().updateMessage(sid, rid, {
        display: "runtime",
        function: d.function,
      });
    }
    applyStreamEvent(sid, rid, d.event);
    return;
  }
  if (d.type === "result" || d.type === "error" || d.type === "cancelled") {
    finalize(sid, rid, d);
  }
}

/** True iff the parent_id refers to an assistant ChatMsg already in the
 *  store. LLM-issued @agentic_function runtime blocks have parent_id =
 *  assistant reply id; fn-form / direct-run runtime blocks have
 *  parent_id = user msg id. Only the former gets merged INSIDE the
 *  assistant bubble; the latter stays as a top-level row. */
function _isAssistantParent(parentId: string | undefined): boolean {
  if (!parentId) return false;
  const p = useSessionStore.getState().messagesById[parentId];
  return !!p && p.role === "assistant";
}

/** Push or replace a runtime child inside its parent assistant's
 *  ``runtimeChildren`` array. Mutation goes through
 *  ``updateMessage`` so subscribers (the AssistantBubble) re-render. */
function _mergeRuntimeIntoParent(
  sid: string,
  parentId: string,
  child: ChatMsg,
): void {
  const store = useSessionStore.getState();
  const parent = store.messagesById[parentId];
  if (!parent) return;
  const existing = parent.runtimeChildren ?? [];
  const idx = existing.findIndex((c) => c.id === child.id);
  const nextChildren =
    idx >= 0
      ? existing.map((c, i) => (i === idx ? { ...c, ...child } : c))
      : [...existing, child];
  store.updateMessage(sid, parentId, { runtimeChildren: nextChildren });
}

/** Materialize / update a runtime-block row for an LLM-issued
 *  @agentic_function call. Two routing paths:
 *    - parent is an assistant ChatMsg → merge into the assistant's
 *      ``runtimeChildren`` so the bubble renders the runtime card
 *      inside its own body (no separate top-level row).
 *    - parent is a user / unknown row (fn-form, direct /api/function/)
 *      → fall back to the legacy behaviour: own ChatMsg, rendered as a
 *      standalone <RuntimeBlock /> by <MessageList />. */
function handleRuntimeRow(sid: string, d: ChatResponseData): void {
  if (!d.msg_id) return;
  const store = useSessionStore.getState();
  const existing = store.messagesById[d.msg_id];
  const parentId = d.parent_id;
  const mergeIntoAssistant = _isAssistantParent(parentId);

  if (d.type === "status") {
    // First sighting — create the row. Idempotent on duplicate
    // broadcasts (e.g. cross-tab re-emit).
    if (existing) return;
    const child: ChatMsg = {
      id: d.msg_id,
      role: "assistant",
      content: "",
      display: "runtime",
      function: d.function,
      status: d.status === "running" ? "running" : "streaming",
      parentId,
      timestamp: Date.now(),
    };
    if (mergeIntoAssistant && parentId) {
      _mergeRuntimeIntoParent(sid, parentId, child);
    } else {
      // Parent is user / unknown / not-yet-present — keep the legacy
      // top-level row. If the assistant parent shows up between
      // status and result, the result branch will migrate the row
      // into the parent's runtimeChildren and remove it from order.
      store.appendMessage(sid, child);
    }
    return;
  }
  // type === "result": finalize the row with the rebuilt DAG + return
  // text.
  const patch: Partial<ChatMsg> = {
    content: d.content ?? "",
    display: "runtime",
    function: d.function ?? existing?.function,
    status: "done",
    rawType: d.type,
    contextTree: (d.context_tree as never) || undefined,
    timestamp: Date.now(),
  };
  if (mergeIntoAssistant && parentId) {
    // Update inside the parent's runtimeChildren.
    const parent = store.messagesById[parentId];
    const list = parent?.runtimeChildren ?? [];
    const idx = list.findIndex((c) => c.id === d.msg_id);
    if (idx >= 0) {
      const next = list.map((c, i) =>
        i === idx ? { ...c, ...patch } : c,
      );
      store.updateMessage(sid, parentId, { runtimeChildren: next });
    } else {
      const child: ChatMsg = {
        id: d.msg_id,
        role: "assistant",
        content: patch.content ?? "",
        display: "runtime",
        function: patch.function,
        status: "done",
        rawType: patch.rawType,
        contextTree: patch.contextTree,
        timestamp: patch.timestamp,
        parentId,
      };
      _mergeRuntimeIntoParent(sid, parentId, child);
    }
    // Also clean up any top-level copy from the status branch (when
    // the assistant parent didn't yet exist at status time).
    if (store.messagesById[d.msg_id]) {
      // Remove the standalone row from message order.
      useSessionStore.setState((s) => {
        const order = s.messageOrder[sid];
        if (!order) return {};
        const i = order.indexOf(d.msg_id!);
        if (i < 0) return {};
        const nextOrder = [...order];
        nextOrder.splice(i, 1);
        const byId = { ...s.messagesById };
        delete byId[d.msg_id!];
        return {
          messagesById: byId,
          messageOrder: { ...s.messageOrder, [sid]: nextOrder },
        };
      });
    }
    return;
  }
  if (existing) {
    store.updateMessage(sid, d.msg_id, patch);
  } else {
    store.appendMessage(sid, {
      id: d.msg_id,
      role: "assistant",
      parentId,
      content: patch.content ?? "",
      display: "runtime",
      function: patch.function,
      status: "done",
      rawType: patch.rawType,
      contextTree: patch.contextTree,
      timestamp: patch.timestamp,
    });
  }
}

function handleUserMessage(sid: string, d: ChatResponseData): void {
  if (!d.msg_id) return;
  const store = useSessionStore.getState();
  if (store.messagesById[d.msg_id]) return;
  store.appendMessage(sid, {
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
  const cur = ensureReply(sid, rid);

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
      // Agentic tools (gui_agent / research_agent / wiki_agent) render
      // as their own RuntimeBlock row (via handleRuntimeRow) — skip
      // the folded chat-tool card so they don't appear twice.
      if (evt.tool && AGENTIC_TOOL_NAMES.has(evt.tool)) {
        store.updateMessage(sid, rid, { status: "streaming" });
        break;
      }
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
      // Agentic tool results were never pushed in tool_use above —
      // nothing to update here. The runtime row takes care of the
      // result display.
      if (evt.tool && AGENTIC_TOOL_NAMES.has(evt.tool)) break;
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
  const cur = ensureReply(sid, rid);

  const status: ChatMsg["status"] =
    d.type === "error"
      ? "error"
      : d.type === "cancelled" || d.cancelled
        ? "cancelled"
        : "done";

  const patch: Partial<ChatMsg> = { status, rawType: d.type };
  if (status === "error") {
    if (d.reason) patch.errorReason = d.reason;
    if (typeof d.retryable === "boolean") patch.errorRetryable = d.retryable;
    if (typeof d.retry_after_s === "number") patch.errorRetryAfterS = d.retry_after_s;
  }
  if (d.function) patch.function = d.function;
  if (d.display) patch.display = d.display;
  // A `/run` result carries the execution tree, usage and attempt
  // history that the runtime block renders in its body / footer.
  if (d.context_tree) patch.contextTree = d.context_tree as never;
  if (d.usage) patch.usage = d.usage;
  if (d.attempts) patch.attempts = d.attempts as never[];
  if (typeof d.current_attempt === "number") {
    patch.current_attempt = d.current_attempt;
  }

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
