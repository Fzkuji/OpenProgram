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
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { promoteToHead } from "@/lib/state/send-queue";
import { stopSession } from "@/components/chat/composer/submit/use-chat-submit";
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
import { QueuedMessages } from "./queued-messages";
import { MessageTimestamp } from "./message-actions";

/** goal 循环的内部 spawn 轮 label（openprogram/programs/workflow/goal/
 *  __init__.py 的 run_agent_turn(label=...)，经 spawnedFrom.label 到达）。
 *  只有这些轮才做 JSON 尾巴折叠——绝不按"内容长得像 JSON"匹配普通消息。 */
const GOAL_SPAWN_LABELS = new Set(["goal 判定", "goal 完善"]);

/** 从回复文本里剥出结尾的严格 JSON 对象。找最左的 "{" 使其到结尾能
 *  JSON.parse 成对象 —— 即整个 JSON 尾巴；前面的部分是 prose。 */
function splitJsonTail(content: string): {
  prose: string;
  json: string;
  data: Record<string, unknown>;
} | null {
  const t = content.trimEnd();
  if (!t.endsWith("}")) return null;
  for (let i = t.indexOf("{"); i !== -1; i = t.indexOf("{", i + 1)) {
    const tail = t.slice(i);
    try {
      const data = JSON.parse(tail) as unknown;
      if (data && typeof data === "object" && !Array.isArray(data)) {
        return { prose: t.slice(0, i).trim(), json: tail, data: data as Record<string, unknown> };
      }
    } catch {
      /* not a JSON start — keep scanning */
    }
    // ponytail: O(n·parse) scan; content is one LLM reply, never large.
  }
  return null;
}

/** goal 判定回复的完整五键契约（goal/__init__.py _parse_decision）。
 *  主 lane 的模型有时会把上下文里见过的判定 JSON 依样画在回复结尾——
 *  按这个精确 schema 识别（met/need_user 布尔 + reason/question 字符串
 *  + options 数组，五键齐全），不是"内容长得像 JSON"的泛匹配：普通
 *  消息几乎不可能撞出这个形状。 */
function isVerdictShape(d: Record<string, unknown>): boolean {
  return (
    typeof d.met === "boolean" &&
    typeof d.need_user === "boolean" &&
    typeof d.reason === "string" &&
    typeof d.question === "string" &&
    Array.isArray(d.options)
  );
}

/** Assistant 行的包装层：把回复结尾的 goal 机器 JSON 收进 <details>，
 *  正文只显示 prose。两条识别路径（都不碰持久化数据，纯渲染层）：
 *  1) 内部 spawn 轮 —— spawn 根用户行的 spawnedFrom.label ∈
 *     GOAL_SPAWN_LABELS，经本行 calledBy/predecessor 关联；
 *  2) 主 lane 回复尾部被模型依样画出的判定 JSON —— 精确五键
 *     verdict schema（见 isVerdictShape）。
 *  展开后仍是原始 JSON（调试）。其余消息原样走 AssistantBubble。 */
function AssistantMessage({
  msg,
  sessionIdOverride,
}: {
  msg: ChatMsg;
  sessionIdOverride?: string;
}) {
  const { text } = useTranslation();
  const spawnLabel = useSessionStore((s) =>
    msg.calledBy ? s.messagesById[msg.calledBy]?.spawnedFrom?.label : undefined,
  );
  const isGoalSpawn = !!spawnLabel && GOAL_SPAWN_LABELS.has(spawnLabel);
  // streaming 期间不折（JSON 尾巴没到齐会闪）；落定后一次成型。
  const settled = msg.status !== "streaming" && msg.status !== "running"
    && msg.status !== "pending";
  const split = settled ? splitJsonTail(msg.content || "") : null;
  if (!split || (!isGoalSpawn && !isVerdictShape(split.data))) {
    return <AssistantBubble msg={msg} sessionIdOverride={sessionIdOverride} />;
  }
  // blocks 路径渲染的是 text 块不是 content —— 同步剥掉最后一个 text
  // 块的 JSON 尾巴，两条渲染路径一致。
  let blocks = msg.blocks;
  if (blocks) {
    for (let i = blocks.length - 1; i >= 0; i--) {
      const b = blocks[i];
      if (b.type !== "text") continue;
      const bt = (b.text || "").trimEnd();
      if (bt.endsWith(split.json)) {
        const stripped = bt.slice(0, bt.length - split.json.length).trim();
        blocks = [
          ...blocks.slice(0, i),
          ...(stripped ? [{ ...b, text: stripped }] : []),
          ...blocks.slice(i + 1),
        ];
      }
      break;
    }
  }
  const met = split.data.met;
  const reason = typeof split.data.reason === "string" ? split.data.reason : "";
  const summary =
    typeof met === "boolean"
      ? (met
          ? text("Verdict: met", "裁决：已达成")
          : text("Verdict: not met", "裁决：未达成"))
        + (reason ? ` · ${reason.length > 80 ? reason.slice(0, 80) + "…" : reason}` : "")
      : text("Structured output", "结构化输出");
  return (
    <AssistantBubble
      msg={{ ...msg, content: split.prose, blocks }}
      verdict={{ summary, json: JSON.stringify(split.data, null, 2) }}
      sessionIdOverride={sessionIdOverride}
    />
  );
}

function dispatch(msg: ChatMsg, sessionIdOverride?: string) {
  if (msg.role === "system") {
    return (
      <div className="message system" data-msg-id={msg.id}>
        {msg.content}
        <div className="message-actions-footer">
          <div className="message-actions">
            <MessageTimestamp timestamp={msg.timestamp} />
          </div>
        </div>
      </div>
    );
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
    // 时间线组件（根行 + 递归子树，子节点逐层展开）。
    return (
      <div className="runtime-card-host">
        <RuntimeBlock msg={msg} />
      </div>
    );
  }
  if (msg.role === "user") {
    return <UserBubble msg={msg} />;
  }
  return <AssistantMessage msg={msg} sessionIdOverride={sessionIdOverride} />;
}

export const MessageRow = memo(function MessageRow({
  id,
  sessionIdOverride,
}: {
  id: string;
  sessionIdOverride?: string;
}) {
  const msg = useMessageById(id);
  if (!msg) return null;
  return dispatch(msg, sessionIdOverride);
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
function PendingReplyIndicator({ timestamp }: { timestamp?: number }) {
  const { text } = useTranslation();
  // Same avatar as the assistant bubble that replaces this on the first
  // delta (same .message-header placement, same profile config), so the
  // agent identity is continuous from the moment the user hits send —
  // no logo blink-out during the transient "thinking…" state.
  const profile = useAgentProfile();
  const [fallbackTimestamp] = useState(() => Date.now());
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
        /* Short, discrete turn status ("thinking…") — the one thing in
           the transcript worth announcing. Streaming token deltas are
           deliberately left silent (see assistant-bubble). */
        role="status"
        aria-live="polite"
      >
        <span className="thinking-spinner" aria-hidden="true" />
        <span className="pending-label">{text("thinking…", "思考中…")}</span>
        <MessageTimestamp timestamp={timestamp ?? fallbackTimestamp} />
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

  // Parent-return actions: once the reload has rows on screen, reveal the
  // original sub-agent timeline row. Collapsed strips unmount their children,
  // so first open the exact strip indexed by branch head, then locate the row.
  // The flag is consumed only when the reveal actually runs, so a
  // mid-hydration re-render just reschedules it.
  useEffect(() => {
    if (!runtimeState._pendingExpandAttach || ids.length === 0) return;
    const esc = (v: string) =>
      window.CSS && CSS.escape ? CSS.escape(v) : v;
    const t = window.setTimeout(() => {
      const pend = runtimeState._pendingExpandAttach;
      if (!pend) return;
      runtimeState._pendingExpandAttach = null;
      const anchorEl = document.querySelector(
        '[data-msg-id="' + esc(pend.anchor) + '"], [data-msg-ids~="'
        + esc(pend.anchor) + '"]',
      ) as HTMLElement | null;
      anchorEl?.scrollIntoView({ block: "center" });
      const strip = anchorEl?.querySelector(
        '.tl[data-subagent-heads~="' + esc(pend.head) + '"]',
      ) as HTMLElement | null;
      if (strip?.getAttribute("data-open") === "0") {
        (strip.querySelector('.tl-toggle') as HTMLElement | null)?.click();
      }
      window.setTimeout(() => {
        const row = document.querySelector(
          '.tl-step[data-head-id="' + esc(pend.head) + '"]',
        ) as HTMLElement | null;
        const target = row || anchorEl;
        if (!target) return;
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("dag-flash");
        window.setTimeout(() => target.classList.remove("dag-flash"), 1400);
      }, 280);
    }, 250);
    return () => window.clearTimeout(t);
  }, [ids]);

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

  // "Stop current and send now" on a queued row: promote it to the head
  // of the queue, then stop the run. The stop clears the running task,
  // which is what triggers the queue drain — so the promoted message is
  // the one that goes out, and everything ahead of it just waits its
  // turn behind it instead of being dropped.
  const stopAndSend = useCallback(
    (queuedId: string) => {
      if (!sessionId) return;
      promoteToHead(sessionId, queuedId);
      stopSession(sessionId, (payload) => {
        const sock = getSocket();
        if (!sock || sock.readyState !== WebSocket.OPEN) return false;
        sock.send(JSON.stringify(payload));
        return true;
      });
    },
    [sessionId],
  );

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
      {showPending ? (
        <PendingReplyIndicator timestamp={runningTask?.started_at} />
      ) : null}
      {/* Messages typed during the run — dimmed rows under the live
          turn, drained one at a time when it ends. */}
      <QueuedMessages sessionId={sessionId} onStopAndSend={stopAndSend} />
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
