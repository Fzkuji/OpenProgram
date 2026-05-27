"use client";

/**
 * Message list — the React message stream.
 *
 * Portaled into `#messages-mount` (a `display:contents` host inside the
 * legacy `#chatMessages` container), so each rendered bubble becomes a
 * direct flex child of `#chatMessages` — the same layout the legacy
 * renderer produced.
 *
 * The active conversation comes from the store's `currentSessionId`,
 * kept in sync by the `chat_ack` reducer and the route effect in
 * `app-shell.tsx`. Each `MessageRow` subscribes to its own message
 * entry so a streaming delta re-renders only the affected bubble.
 */
import { memo, useEffect } from "react";

import {
  useMessageById,
  useMessageIds,
  useSessionStore,
  type ChatMsg,
} from "@/lib/session-store";

import { AssistantBubble } from "./assistant-bubble";
import { AttachCard } from "./attach-card";
import { RuntimeBlock } from "./runtime-block";
import { SpawnedFromCard } from "./spawned-from-card";
import { UserBubble } from "./user-bubble";

function dispatch(msg: ChatMsg) {
  if (msg.role === "system") {
    return <div className="message system">{msg.content}</div>;
  }
  if (msg.role === "assistant" && msg.function === "attach") {
    return (
      <div className="attach-row" data-msg-id={msg.id}>
        <AttachCard msg={msg} />
      </div>
    );
  }
  if (msg.role === "user" && msg.spawnedFrom) {
    return (
      <>
        <div className="attach-row" data-spawned-root={msg.id}>
          <SpawnedFromCard msg={msg} />
        </div>
        <UserBubble msg={msg} />
      </>
    );
  }
  if (msg.display === "runtime") {
    if (msg.role === "user") return null;
    return <RuntimeBlock msg={msg} />;
  }
  if (msg.role === "user") {
    return <UserBubble msg={msg} />;
  }
  return <AssistantBubble msg={msg} />;
}

const MessageRow = memo(function MessageRow({ id }: { id: string }) {
  const msg = useMessageById(id);
  if (!msg) return null;
  return dispatch(msg);
});

/** Pin `#chatArea` to the bottom as `#chatMessages` grows, unless the
 *  user has scrolled up. Observes the container rather than threading a
 *  dependency through, so both new bubbles and streamed text deltas
 *  keep the viewport at the bottom.
 *
 *  Also runs KaTeX over freshly-streamed bubbles. ``renderMd`` only
 *  parses markdown-it; it leaves ``\[ ... \]`` / ``$$...$$`` deltas
 *  raw, marked with a ``.md-rendered`` span. The legacy
 *  ``renderMathInChat`` (called by ``scrollToBottom`` in the legacy
 *  path) is what actually swaps math delimiters for KaTeX HTML. The
 *  React message-list never calls ``scrollToBottom`` so streaming
 *  bubbles stayed unrendered until something else (the next send,
 *  page refresh, ...) triggered the legacy hook. Fire it on every
 *  container resize so React-side updates show math live.
 *
 *  ``newTurnSeed`` (changes when message count grows) force-resets
 *  the stuck flag — sending or receiving a new turn pulls focus back
 *  to the bottom even if the user had scrolled up earlier.
 */
function useChatAreaStick(newTurnSeed: number) {
  useEffect(() => {
    const area = document.getElementById("chatArea");
    const msgs = document.getElementById("chatMessages");
    if (!area || !msgs) return;
    let stuck = true;
    const pin = () => {
      const w = window as unknown as { renderMathInChat?: () => void };
      try { w.renderMathInChat?.(); } catch { /* ignore */ }
      if (stuck) area.scrollTop = area.scrollHeight;
    };
    const onScroll = () => {
      stuck = area.scrollHeight - area.scrollTop - area.clientHeight < 80;
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    const ro = new ResizeObserver(pin);
    ro.observe(msgs);
    return () => {
      area.removeEventListener("scroll", onScroll);
      ro.disconnect();
    };
  }, []);
  // Force re-stick whenever a new turn arrives — chat composer / a
  // streamed assistant reply both bump ``newTurnSeed`` so this hook
  // pulls scroll back to the latest content. After this, the user can
  // freely scroll up; the ResizeObserver only auto-pins while still
  // within 80px of the bottom.
  useEffect(() => {
    const area = document.getElementById("chatArea");
    if (!area) return;
    area.scrollTop = area.scrollHeight;
  }, [newTurnSeed]);
}

/** Standalone breathing-dot indicator shown between a user msg and
 *  the (yet-to-arrive) assistant reply.
 *
 *  Window: from chat send / chat_ack to the first assistant delta.
 *  Without this the user sees an empty chat tail while waiting on
 *  network + LLM warmup — feels stuck.
 *
 *  Conditions:
 *    1. session has a running task (we're waiting on something)
 *    2. last message is a user turn (no assistant placeholder yet, or
 *       the placeholder is itself a user-runtime anchor like fn-form)
 *
 *  Once any assistant row lands, that bubble's own TypingIndicator
 *  takes over and this standalone one is hidden.
 */
function PendingReplyIndicator() {
  return (
    <div className="message assistant pending-standalone">
      <div className="typing-indicator">
        <div className="dot" />
        <div className="dot" />
        <div className="dot" />
      </div>
    </div>
  );
}

export function MessageList() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const ids = useMessageIds(sessionId);
  const runningTask = useSessionStore((s) =>
    sessionId ? s.runningTasks[sessionId] ?? null : null,
  );
  const messagesById = useSessionStore((s) => s.messagesById);
  useChatAreaStick(ids.length);

  // Show the standalone indicator only between "user sent → assistant
  // reply created". Once any assistant-role msg appears tailing the
  // list, the assistant bubble's own TypingIndicator covers it.
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const lastMsg = lastId ? messagesById[lastId] : null;
  const showPending =
    runningTask !== null
    && lastMsg !== null
    && lastMsg !== undefined
    && lastMsg.role === "user";

  return (
    <>
      {ids.map((id) => (
        <MessageRow key={id} id={id} />
      ))}
      {showPending ? <PendingReplyIndicator /> : null}
    </>
  );
}
