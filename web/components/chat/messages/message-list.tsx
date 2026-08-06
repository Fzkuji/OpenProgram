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
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ArrowDown } from "lucide-react";

import {
  useMessageById,
  useMessageIds,
  useSessionStore,
  type ChatMsg,
} from "@/lib/session-store";

import { useTranslation } from "@/lib/i18n";
import { useAgentProfile } from "@/lib/format-utils/agent-style";
import {
  readChatScroll,
  resolveChatScrollTop,
  writeChatScroll,
} from "@/lib/state/chat-scroll";
import { Avatar } from "@/components/avatar";

import { AssistantBubble } from "./assistant-bubble";
import { AttachCard } from "./attach-card";
import { MessageRail } from "./message-rail";
import { AgentBranchBanner } from "./agent-branch-banner";
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
    // 手动函数运行（fn-form / /run）：RuntimeBlock 内部已统一为
    // 时间线组件（根行 + 递归子树，默认全展开）。
    return (
      <div className="runtime-card-host">
        <RuntimeBlock msg={msg} />
      </div>
    );
  }
  if (msg.role === "user") {
    return <UserBubble msg={msg} />;
  }
  return <AssistantBubble msg={msg} />;
}

export const MessageRow = memo(function MessageRow({ id }: { id: string }) {
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
 *  ``newTurnSeed`` (changes when message count grows) marks a new turn.
 *  Following it is conditional: a reader parked at the bottom is carried
 *  along, a reader who scrolled up to re-read something keeps their
 *  place. Their own send always follows — that is an explicit gesture,
 *  not something arriving at them.
 */
function useChatAreaStick(
  chatKey: string | null,
  newTurnSeed: number,
  ownTurn: boolean,
) {
  const activeKeyRef = useRef<string | null>(chatKey);
  const previousKeyRef = useRef<string | null>(null);
  const previousSeedRef = useRef(newTurnSeed);
  const stuckRef = useRef(true);
  const lastPointerRef = useRef(0);
  const scrollTopRef = useRef(0);
  // The ref drives the scroll math on every event; this mirrors it into
  // render state so the "jump to latest" affordance can appear. Set only
  // on transitions, so ordinary scrolling doesn't re-render per frame.
  const [detached, setDetached] = useState(false);

  useEffect(() => {
    const area = document.getElementById("chatArea");
    const msgs = document.getElementById("chatMessages");
    if (!area || !msgs) return;
    // A click that expands/collapses something (execution strip, thinking
    // row) resizes the container; pinning then yanks the clicked element
    // upward. Suppress the pin briefly after any pointer interaction so
    // user-initiated growth expands downward in place.
    const pin = () => {
      // `window.renderMathInChat` was defined by the legacy public/js
      // bundle, which no longer exists — the read was permanently
      // undefined. Math rendering now lives in the markdown pipeline.
      if (stuckRef.current && performance.now() - lastPointerRef.current > 600) {
        area.scrollTop = area.scrollHeight;
        scrollTopRef.current = area.scrollTop;
        const key = activeKeyRef.current;
        if (key) writeChatScroll(window.sessionStorage, key, area.scrollTop);
      }
    };
    const onScroll = () => {
      const atBottom =
        area.scrollHeight - area.scrollTop - area.clientHeight < 80;
      stuckRef.current = atBottom;
      setDetached((was) => (was === !atBottom ? was : !atBottom));
      scrollTopRef.current = area.scrollTop;
      const key = activeKeyRef.current;
      if (key) writeChatScroll(window.sessionStorage, key, area.scrollTop);
    };
    const onPointer = () => { lastPointerRef.current = performance.now(); };
    area.addEventListener("scroll", onScroll, { passive: true });
    area.addEventListener("pointerdown", onPointer, { passive: true });
    const ro = new ResizeObserver(pin);
    ro.observe(msgs);
    return () => {
      area.removeEventListener("scroll", onScroll);
      area.removeEventListener("pointerdown", onPointer);
      ro.disconnect();
    };
  }, []);

  // Save the outgoing position and restore the incoming one before paint.
  // `chatKey` is part of the dependency so equal-length conversations still
  // switch correctly. Whether a new turn in the same chat returns to the
  // bottom depends on where the reader was — see `resolveChatScrollTop`.
  useLayoutEffect(() => {
    const area = document.getElementById("chatArea");
    if (!area) return;
    const keyChanged = previousKeyRef.current !== chatKey;
    const seedChanged = previousSeedRef.current !== newTurnSeed;
    if (previousKeyRef.current && keyChanged) {
      writeChatScroll(
        window.sessionStorage,
        previousKeyRef.current,
        scrollTopRef.current,
      );
    }
    activeKeyRef.current = chatKey;
    previousKeyRef.current = chatKey;
    previousSeedRef.current = newTurnSeed;

    const saved = keyChanged && chatKey
      ? readChatScroll(window.sessionStorage, chatKey)
      : null;
    area.scrollTop = resolveChatScrollTop({
      keyChanged,
      seedChanged,
      saved,
      scrollHeight: area.scrollHeight,
      currentTop: area.scrollTop,
      atBottom: stuckRef.current,
      ownTurn,
    });
    scrollTopRef.current = area.scrollTop;
    // Recompute rather than assume: after a follow we are at the bottom,
    // and after a deliberate stay-put we are not — and it is this flag
    // that decides whether the streaming deltas keep pinning.
    stuckRef.current =
      area.scrollHeight - area.scrollTop - area.clientHeight < 80;
    setDetached(!stuckRef.current);
  }, [chatKey, newTurnSeed, ownTurn]);

  const jumpToLatest = useCallback(() => {
    const area = document.getElementById("chatArea");
    if (!area) return;
    area.scrollTo({ top: area.scrollHeight, behavior: "smooth" });
    // Re-attach immediately rather than waiting for the smooth scroll to
    // finish: a delta landing mid-animation should already be followed,
    // and the button should not linger over a view that is on its way down.
    stuckRef.current = true;
    setDetached(false);
  }, []);

  return { detached, jumpToLatest };
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
  const { text } = useTranslation();
  // Same avatar as the assistant bubble that replaces this on the first
  // delta (same .message-header placement, same profile config), so the
  // agent identity is continuous from the moment the user hits send —
  // no logo blink-out during the transient "thinking…" state.
  const profile = useAgentProfile();
  return (
    <div className="message assistant pending-standalone">
      <div className="message-header">
        <Avatar
          className="message-avatar bot-avatar"
          size={28}
          radius={8}
          name={profile.name}
          config={
            profile.avatar ?? {
              kind: "dicebear",
              style: "shapes",
              seed: profile.name,
            }
          }
        />
      </div>
      <div
        className="pending-body"
        style={{ paddingLeft: 36 }}
      >
        <span className="thinking-spinner" aria-hidden="true" />
        <span className="pending-label">{text("thinking…", "思考中…")}</span>
      </div>
    </div>
  );
}

/** claude.ai-style transcript skeleton — one user-bubble block top
 *  right, then progressively shorter grey bars. Shown while a session
 *  switch is waiting on the load_session reply (no full cache). */
function TranscriptSkeleton() {
  return (
    <div className="transcript-skeleton" aria-hidden="true">
      <div className="skeleton-bubble" />
      <div className="skeleton-bar" style={{ width: "88%" }} />
      <div className="skeleton-bar" style={{ width: "95%" }} />
      <div className="skeleton-bar" style={{ width: "72%" }} />
      <div className="skeleton-bar" style={{ width: "90%" }} />
      <div className="skeleton-bar" style={{ width: "58%" }} />
      <div className="skeleton-bar" style={{ width: "34%" }} />
    </div>
  );
}

export function MessageList() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const chatKey = useSessionStore((s) => s.activeChatKey);
  const ids = useMessageIds(sessionId);
  const runningTask = useSessionStore((s) =>
    sessionId ? s.runningTasks[sessionId] ?? null : null,
  );
  // Only the LAST row's role matters here (see ``showPending`` below).
  // Subscribing to the whole ``messagesById`` map would re-render this
  // component — and re-map every id — on every single streaming delta,
  // because ``updateMessage`` returns a fresh map object each time.
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const lastRole = useSessionStore((s) =>
    lastId ? (s.messagesById[lastId]?.role ?? null) : null,
  );
  const loadingId = useSessionStore((s) => s.transcriptLoadingId);
  // `lastRole === "user"` means the row that just arrived is the reader's
  // own send — that follows to the bottom unconditionally, unlike an
  // agent row, which only follows if they were already down there.
  const { detached, jumpToLatest } = useChatAreaStick(
    chatKey,
    ids.length,
    lastRole === "user",
  );

  // Fade the transcript in once per session switch. The ref remembers
  // which session already faded, so streaming updates (ids.length
  // growing) inside the same session don't re-trigger the animation.
  const lastFadedSession = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId || ids.length === 0) return;
    if (lastFadedSession.current === sessionId) return;
    lastFadedSession.current = sessionId;
    const el = document.getElementById("chatMessages");
    if (!el) return;
    el.classList.add("session-enter");
    const t = setTimeout(() => el.classList.remove("session-enter"), 220);
    return () => {
      clearTimeout(t);
      el.classList.remove("session-enter");
    };
  }, [sessionId, ids.length]);

  // Show the standalone indicator while we're still waiting on the
  // turn — either:
  //   * the assistant placeholder hasn't landed yet (last msg is the
  //     user turn we just sent), OR
  //   * the placeholder exists but is still empty (chat_ack landed
  //     but no text/thinking/tool deltas yet)
  // Once the bubble has ANY content, that bubble's own
  // TypingIndicator / streaming text takes over and we hide.
  // Only show the STANDALONE indicator when there's no assistant
  // placeholder yet — i.e. between user send and ``chat_ack``. Once
  // the assistant bubble exists (even empty), its own
  // ``TypingIndicator`` handles the empty-streaming state. Without
  // this guard the user sees two stacked "Agentic" rows: the real
  // placeholder bubble + the standalone, double-rendering.
  const showPending = runningTask !== null && lastRole === "user";

  // Session switch with nothing cached yet: skeleton placeholder
  // instead of an empty area / welcome flash. Minimap etc. wait too.
  if (sessionId && loadingId === sessionId && ids.length === 0) {
    return <TranscriptSkeleton />;
  }

  return (
    <>
      <AgentBranchBanner />
      <MessageRail />
      {ids.map((id) => (
        <MessageRow key={id} id={id} />
      ))}
      {showPending ? <PendingReplyIndicator /> : null}
      {detached && ids.length > 0 ? (
        <div className="jump-latest-anchor">
          <button
            type="button"
            className="jump-latest"
            onClick={jumpToLatest}
            title={text("Jump to latest", "跳到最新")}
          >
            <ArrowDown aria-hidden />
            {text("Jump to latest", "跳到最新")}
          </button>
        </div>
      ) : null}
    </>
  );
}
