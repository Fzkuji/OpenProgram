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
import { RuntimeBlock } from "./runtime-block";
import { UserBubble } from "./user-bubble";

function dispatch(msg: ChatMsg) {
  if (msg.role === "system") {
    return <div className="message system">{msg.content}</div>;
  }
  // Attach pointer rows have display="runtime" + function="attach"
  // but want the AttachCard, not the generic RuntimeBlock. Route
  // before the runtime branch so the card wins.
  if (msg.role === "assistant" && msg.function === "attach") {
    return <AssistantBubble msg={msg} />;
  }
  if (msg.display === "runtime") {
    // A `/run` turn renders as ONE runtime block, owned by the
    // assistant reply (it carries the result + the `function`
    // signature). The paired user message only holds the raw command
    // — drop it so the command isn't shown twice.
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
 *  keep the viewport at the bottom. */
function useChatAreaStick() {
  useEffect(() => {
    const area = document.getElementById("chatArea");
    const msgs = document.getElementById("chatMessages");
    if (!area || !msgs) return;
    let stuck = true;
    const pin = () => {
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
}

export function MessageList() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const ids = useMessageIds(sessionId);
  useChatAreaStick();

  return (
    <>
      {ids.map((id) => (
        <MessageRow key={id} id={id} />
      ))}
    </>
  );
}
