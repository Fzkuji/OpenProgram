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

import { agentInitial, useAgentProfile } from "@/lib/agent-style";

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

/** Breathing "<Agent> is thinking…" indicator shown between a user
 *  msg and the (yet-to-arrive) assistant reply (or an assistant
 *  bubble that exists but is still empty).
 *
 *  Once the bubble has ANY content (text, thinking, tool, runtime
 *  child), that bubble's own streaming UI takes over and this is
 *  hidden by ``MessageList``.
 */
function PendingReplyIndicator() {
  const profile = useAgentProfile();
  return (
    <div className="message assistant pending-standalone">
      <div className="message-header">
        <div
          className="message-avatar bot-avatar"
          style={{ background: profile.color, color: "#fff" }}
        >
          {profile.initial || agentInitial(null)}
        </div>
        <div className="message-sender">{profile.name}</div>
      </div>
      <div className="chat-stream-body pending-body">
        <span className="pending-pulse" aria-hidden="true" />
        <span className="pending-label">{`${profile.name} is thinking…`}</span>
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

  // Show the standalone indicator while we're still waiting on the
  // turn — either:
  //   * the assistant placeholder hasn't landed yet (last msg is the
  //     user turn we just sent), OR
  //   * the placeholder exists but is still empty (chat_ack landed
  //     but no text/thinking/tool deltas yet)
  // Once the bubble has ANY content, that bubble's own
  // TypingIndicator / streaming text takes over and we hide.
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const lastMsg = lastId ? messagesById[lastId] : null;
  // Only show the STANDALONE indicator when there's no assistant
  // placeholder yet — i.e. between user send and ``chat_ack``. Once
  // the assistant bubble exists (even empty), its own
  // ``TypingIndicator`` handles the empty-streaming state. Without
  // this guard the user sees two stacked "Agentic" rows: the real
  // placeholder bubble + the standalone, double-rendering.
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
