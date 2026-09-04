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
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
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
  animateJumpToLatest,
  isChatAtBottom,
  readBottomPadding,
  readComposerHeight,
  readChatScroll,
  resolveChatScrollTop,
  writeChatScroll,
} from "@/lib/state/chat-scroll";
import {
  RECYCLE_MIN_ROWS,
  collectAlwaysLive,
  decideLiveRows,
  foldKey,
  getRowHeight,
  heightsFor,
  noteChatWidth,
  setRowHeight,
  type WindowNode,
} from "@/lib/state/message-window";
import { Avatar } from "@/components/avatar";
import { showToast } from "@/lib/format-utils/toast";
import { renderMarkdown, useMarkdownReady } from "./markdown";

const JUMP_LATEST_FADE_MS = 280;

import { AssistantBubble } from "./assistant-bubble";
import { AttachCard } from "./attach-card";
import { MessageRail } from "./message-rail";
import { AgentBranchBanner } from "./agent-branch-banner";
import { RuntimeBlock } from "./runtime-block";
import { SpawnedFromCard } from "./spawned-from-card";
import { UserBubble } from "./user-bubble";
import { QueuedMessages } from "./queued-messages";
import { SelfUpdateHistory } from "./self-update-card";
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

const SYSTEM_EVENT_KINDS = new Set(["compaction", "snip", "event"]);

function formatEventTime(timestamp?: number): string {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
    return "";
  }
  const value = new Date(timestamp > 1e12 ? timestamp : timestamp * 1000);
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const originalsOpen = new Set<string>();
const originalsSubs = new Set<() => void>();
const FOLD_MS = 900;

function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function"
    && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function easeScrollBy(area: HTMLElement, delta: number, ms: number): void {
  const start = area.scrollTop;
  const end = Math.max(0, start + delta);
  if (prefersReducedMotion() || end === start) {
    area.scrollTop = end;
    return;
  }
  const t0 = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - t0) / ms);
    const e = t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
    area.scrollTop = start + (end - start) * e;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Keep a collapsing card on screen: only if its top is already above the viewport. */
function easeElTopIntoView(el: Element, ms = 900): void {
  const area = document.getElementById("chatArea");
  if (!area) return;
  const y = el.getBoundingClientRect().top - area.getBoundingClientRect().top;
  if (y < 0) easeScrollBy(area, y - 24, ms);
}

function pinWhile(el: Element, top: number, ms: number): void {
  const area = document.getElementById("chatArea");
  if (!area) return;
  const t0 = performance.now();
  const tick = () => {
    area.scrollTop += el.getBoundingClientRect().top - top;
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function pinBottomWhile(el: Element, ms: number): void {
  const area = document.getElementById("chatArea");
  if (!area) return;
  const y = el.getBoundingClientRect().bottom;
  const t0 = performance.now();
  const tick = () => {
    area.scrollTop += el.getBoundingClientRect().bottom - y;
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function toggleOriginals(cardId: string): void {
  const opening = !originalsOpen.has(cardId);
  const esc = (v: string) => (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(v) : v);
  const bar = document.querySelector(`[data-orig-for="${esc(cardId)}"]`);
  const area = document.getElementById("chatArea");
  const hold = prefersReducedMotion() ? 0 : FOLD_MS + 40;

  const apply = () => {
    if (bar) pinWhile(bar, bar.getBoundingClientRect().top, hold);
    if (opening) originalsOpen.add(cardId);
    else originalsOpen.delete(cardId);
    originalsSubs.forEach((fn) => fn());
  };

  if (opening) {
    apply();
    window.setTimeout(() => {
      if (!area || !bar) return;
      const areaRect = area.getBoundingClientRect();
      const card = document.querySelector(
        `.message.compaction-card[data-msg-id="${esc(cardId)}"]`,
      );
      const barTop = bar.getBoundingClientRect().top;
      const keepBottom = (card ?? bar).getBoundingClientRect().bottom;
      const cardExtent = keepBottom - barTop;
      const target = Math.max(
        24,
        Math.min(area.clientHeight / 2, area.clientHeight - cardExtent - 16),
      );
      const delta = barTop - areaRect.top - target;
      if (Math.abs(delta) >= 8) easeScrollBy(area, delta, FOLD_MS);
    }, hold);
    return;
  }

  // Scroll originals off the top first, then fold so the collapse is off-screen.
  if (!area || !bar || prefersReducedMotion()) {
    apply();
    return;
  }
  const y = bar.getBoundingClientRect().top - area.getBoundingClientRect().top;
  const delta = y - 24;
  if (Math.abs(delta) < 8) {
    apply();
    return;
  }
  easeScrollBy(area, delta, FOLD_MS);
  window.setTimeout(apply, FOLD_MS);
}

function SystemEventRow({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  const n = msg.summarisedCount;
  const tb = msg.tokensBefore;
  const ta = msg.tokensAfter;
  const label = msg.kind === "compaction" && msg.slot === "event" && typeof n === "number"
    ? (tb != null && ta != null
      ? text(
          `Context compacted here: covered ${n} messages, ${tb} → ${ta} tokens`,
          `此处压缩了上下文：盖住 ${n} 条，${tb} → ${ta} tokens`,
        )
      : text(
          `Context compacted here: covered ${n} messages`,
          `此处压缩了上下文：盖住 ${n} 条`,
        ))
    : msg.kind === "compaction" && typeof n === "number"
      ? text(
          `Context compacted: covered ${n} older messages`,
          `上下文已压缩：盖住 ${n} 条旧消息`,
        )
      : msg.content;
  const hm = formatEventTime(msg.timestamp);
  return (
    <div className="message system-event" data-msg-id={msg.id} data-kind={msg.kind} data-slot={msg.slot || ""}>
      <span className="system-event-rule" aria-hidden />
      <span className="system-event-text">
        {label}{hm ? ` · ${hm}` : ""}
      </span>
      <span className="system-event-rule" aria-hidden />
    </div>
  );
}

function CompactionCard({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  useMarkdownReady();
  const cardRef = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    originalsSubs.add(fn);
    return () => { originalsSubs.delete(fn); };
  }, []);
  const n = msg.summarisedCount;
  const hm = formatEventTime(msg.timestamp);
  const showingOrig = originalsOpen.has(msg.id);
  const title = typeof n === "number"
    ? text(`Compacted ${n} earlier messages`, `已压缩 ${n} 条更早的消息`)
    : text("Compacted earlier messages", "已压缩更早的消息");
  const origLabel = showingOrig
    ? text("Hide original messages", "隐藏原始消息")
    : text("Show original messages", "显示原始消息");
  const setCardFull = (next: boolean) => {
    const card = cardRef.current;
    const clip = card?.querySelector(".compaction-card-md-clip") as HTMLElement | null;
    if (!clip || prefersReducedMotion()) {
      setFull(next);
      if (!next && card) easeElTopIntoView(card);
      return;
    }
    if (clip.dataset.anim === "1" || next === full) return;
    const ms = 900;
    const lh = parseFloat(getComputedStyle(clip).getPropertyValue("--comp-md-lh")) || 25.5;
    const start = clip.getBoundingClientRect().height;
    clip.style.transition = "none";
    clip.style.maxHeight = `${start}px`;
    void clip.offsetHeight;
    clip.dataset.anim = "1";
    let end = lh * 7;
    if (next) {
      clip.style.maxHeight = "none";
      end = clip.scrollHeight + 8 + lh;
      clip.style.maxHeight = `${start}px`;
      void clip.offsetHeight;
    }
    if (Math.abs(end - start) < 1) {
      delete clip.dataset.anim;
      clip.style.maxHeight = "";
      clip.style.transition = "";
      setFull(next);
      if (!next && card) easeElTopIntoView(card);
      return;
    }
    requestAnimationFrame(() => {
      const area = document.getElementById("chatArea");
      if (!next && card && area && card.getBoundingClientRect().top < area.getBoundingClientRect().top) {
        pinBottomWhile(clip, ms);
      }
      clip.style.transition = `max-height ${ms}ms ease`;
      clip.style.maxHeight = `${end}px`;
      window.setTimeout(() => {
        clip.style.transition = "none";
        clip.style.maxHeight = `${end}px`;
        setFull(next);
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            delete clip.dataset.anim;
            clip.style.maxHeight = "";
            clip.style.transition = "";
            if (!next && card) easeElTopIntoView(card);
          });
        });
      }, ms);
    });
  };
  return (
    <>
      <div
        className="compaction-card-bar"
        data-orig-for={msg.id}
        data-slot="orig"
        data-open={showingOrig ? "1" : "0"}
      >
        <span className="compaction-card-info">
          {title}{hm ? ` · ${hm}` : ""}
        </span>
        <button
          type="button"
          className="text-hit compaction-orig-toggle"
          onClick={() => toggleOriginals(msg.id)}
        >
          {origLabel}
        </button>
      </div>
      <div
        ref={cardRef}
        className="message compaction-card"
        data-msg-id={msg.id}
        data-kind="compaction"
        data-slot="card"
        data-full={full ? "1" : "0"}
        onClick={(e) => {
          if (!full || (e.target as HTMLElement).closest("a")) return;
          setCardFull(false);
        }}
      >
        <div className="compaction-card-body">
          <div className="compaction-card-md-clip" data-full={full ? "1" : "0"}>
            <div
              className="compaction-card-md"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content || "") }}
            />
            <button
              type="button"
              className="text-hit compaction-card-more"
              onClick={(e) => {
                e.stopPropagation();
                setCardFull(!full);
              }}
            >
              {full
                ? text("Collapse", "收起")
                : text("… Show all", "… 展开全部")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

function dispatch(msg: ChatMsg, sessionIdOverride?: string) {
  if (msg.role === "system") {
    if (msg.kind === "compaction" && msg.slot === "card") {
      return <CompactionCard msg={msg} />;
    }
    if (msg.kind && SYSTEM_EVENT_KINDS.has(msg.kind)) {
      return <SystemEventRow msg={msg} />;
    }
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

export const RecyclableRow = memo(function RecyclableRow({
  id,
  chatKey,
  live,
  onMeasured,
  sessionIdOverride,
}: {
  id: string;
  chatKey: string;
  live: boolean;
  onMeasured: () => void;
  sessionIdOverride?: string;
}) {
  const slotRef = useRef<HTMLDivElement>(null);
  const height = getRowHeight(chatKey, id);
  const show = live || height == null;
  useLayoutEffect(() => {
    const el = slotRef.current;
    if (!el) return;
    if (!show) {
      el.dataset.seen = "1";
      return;
    }
    const write = () => {
      const next = el.offsetHeight;
      if (next > 0 && setRowHeight(chatKey, id, next) === "first") onMeasured();
    };
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    return () => ro.disconnect();
  }, [show, id, chatKey, onMeasured]);
  return (
    <div
      ref={slotRef}
      data-msg-slot={id}
      className="msg-slot"
      style={!show && height != null ? { height } : undefined}
    >
      {show ? (
        <div style={{ display: "contents" }}>
          <MessageRow id={id} sessionIdOverride={sessionIdOverride} />
        </div>
      ) : null}
    </div>
  );
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
  paintRows: boolean,
) {
  const activeKeyRef = useRef<string | null>(chatKey);
  const previousKeyRef = useRef<string | null>(null);
  const previousSeedRef = useRef(newTurnSeed);
  const previousPaintRef = useRef(paintRows);
  const stuckRef = useRef(true);
  const jumpingRef = useRef(false);
  const cancelJumpRef = useRef<(() => void) | null>(null);
  const lastPointerRef = useRef(0);
  const scrollTopRef = useRef(0);
  // The ref drives the scroll math on every event; this mirrors it into
  // render state so the "jump to latest" affordance can appear. Set only
  // on transitions, so ordinary scrolling doesn't re-render per frame.
  const [detached, setDetached] = useState(false);

  useEffect(() => {
    if (!paintRows) return;
    const area = document.getElementById("chatArea");
    const msgs = document.getElementById("chatMessages");
    if (!area || !msgs) return;
    // A click that expands/collapses something (execution strip, thinking
    // row) resizes the container; pinning then yanks the clicked element
    // upward. Suppress the pin briefly after any pointer interaction so
    // user-initiated growth expands downward in place.
    const syncDetached = () => {
      const atBottom = isChatAtBottom(
        area,
        readBottomPadding(msgs),
        readComposerHeight(),
      );
      if (jumpingRef.current) {
        // Stay visible until the ease-in-out ride finishes.
        stuckRef.current = true;
        return atBottom;
      }
      stuckRef.current = atBottom;
      setDetached((was) => (was === !atBottom ? was : !atBottom));
      return atBottom;
    };
    const onScroll = () => {
      if (area.clientHeight <= 0) return;
      syncDetached();
      scrollTopRef.current = area.scrollTop;
      const key = activeKeyRef.current;
      if (key) writeChatScroll(window.sessionStorage, key, area.scrollTop);
    };
    const pin = () => {
      // `window.renderMathInChat` was defined by the legacy public/js
      // bundle, which no longer exists — the read was permanently
      // undefined. Math rendering now lives in the markdown pipeline.
      // A Jump-to-latest click is already smoothing down; snapping
      // scrollTop here fights that and flashes the transcript.
      if (area.clientHeight <= 0) return;
      if (
        stuckRef.current
        && !jumpingRef.current
        && performance.now() - lastPointerRef.current > 600
      ) {
        area.scrollTop = area.scrollHeight;
        scrollTopRef.current = area.scrollTop;
        const key = activeKeyRef.current;
        if (key) writeChatScroll(window.sessionStorage, key, area.scrollTop);
      }
      // Composer / pad growth must re-evaluate "at latest" even when
      // we do not pin — otherwise the button stays up after the last
      // bubble is already above the input.
      syncDetached();
    };
    const onPointer = () => {
      lastPointerRef.current = performance.now();
      if (jumpingRef.current) {
        cancelJumpRef.current?.();
        cancelJumpRef.current = null;
        jumpingRef.current = false;
      }
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    area.addEventListener("pointerdown", onPointer, { passive: true });
    const ro = new ResizeObserver(pin);
    ro.observe(msgs);
    return () => {
      cancelJumpRef.current?.();
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      area.removeEventListener("scroll", onScroll);
      area.removeEventListener("pointerdown", onPointer);
      ro.disconnect();
    };
  }, [paintRows]);

  // Save the outgoing position and restore the incoming one before paint.
  // `chatKey` is part of the dependency so equal-length conversations still
  // switch correctly. Whether a new turn in the same chat returns to the
  // bottom depends on where the reader was — see `resolveChatScrollTop`.
  useLayoutEffect(() => {
    const area = document.getElementById("chatArea");
    if (!area) return;
    if (!paintRows) {
      previousPaintRef.current = false;
      return;
    }
    const becameVisible = previousPaintRef.current === false;
    previousPaintRef.current = true;
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

    const saved = (keyChanged || becameVisible) && chatKey
      ? readChatScroll(window.sessionStorage, chatKey)
      : null;
    // Reveal after a hide must use the same follow/stay rule as a new
    // turn. Preferring `saved` here left a following reader on a stale
    // pixel after the transcript grew in DAG / another pane.
    area.scrollTop = resolveChatScrollTop({
      keyChanged,
      seedChanged: seedChanged || becameVisible,
      saved,
      scrollHeight: area.scrollHeight,
      currentTop: becameVisible
        ? (saved ?? scrollTopRef.current)
        : area.scrollTop,
      atBottom: stuckRef.current,
      ownTurn,
    });
    scrollTopRef.current = area.scrollTop;
    // Recompute rather than assume: after a follow we are at the bottom,
    // and after a deliberate stay-put we are not — and it is this flag
    // that decides whether the streaming deltas keep pinning.
    if (!jumpingRef.current) {
      stuckRef.current = isChatAtBottom(
        area,
        readBottomPadding(document.getElementById("chatMessages")),
        readComposerHeight(),
      );
      setDetached(!stuckRef.current);
    }
  }, [chatKey, newTurnSeed, ownTurn, paintRows]);

  const jumpToLatest = useCallback(() => {
    const area = document.getElementById("chatArea");
    if (!area) return;
    cancelJumpRef.current?.();
    jumpingRef.current = true;
    stuckRef.current = true;
    cancelJumpRef.current = animateJumpToLatest(area, () => {
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      stuckRef.current = true;
      setDetached(false);
    });
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

function WorkspaceAlignmentBanner({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const restoreRequest = useRef<string | null>(null);
  const alignment = useSessionStore((state) =>
    sessionId ? state.conversations[sessionId]?.workspace_alignment : undefined,
  );
  useEffect(() => {
    const clear = (event: Event) => {
      const detail = (event as CustomEvent).detail ?? {};
      if (detail.session_id === sessionId) restoreRequest.current = null;
    };
    window.addEventListener("workspace-alignment-response", clear);
    return () => window.removeEventListener("workspace-alignment-response", clear);
  }, [sessionId]);
  if (!sessionId || alignment?.status !== "mismatch") return null;
  const resolve = (decision: "keep_current_files" | "restore_branch_code") => {
    const socket = getSocket();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      showToast(text("Not connected", "连接已断开"));
      return;
    }
    if (decision === "restore_branch_code" && !restoreRequest.current) {
      restoreRequest.current = crypto.randomUUID();
    }
    socket.send(JSON.stringify({
      action: "resolve_workspace_alignment",
      session_id: sessionId,
      decision,
      idempotency_key: decision === "restore_branch_code"
        ? restoreRequest.current
        : undefined,
      source_head_id: alignment.source_head_id,
      target_head_id: alignment.target_head_id,
    }));
  };
  return (
    <div className="workspace-alignment-banner" role="status">
      <b>{text(
        "Conversation and workspace are not aligned",
        "对话与工作区未对齐",
      )}</b>
      <span>{text(
        "Choose which file state the next editing turn should use.",
        "请选择下一次文件修改要使用的文件状态。",
      )}</span>
      <div>
        <button type="button" onClick={() => resolve("keep_current_files")}>
          {text("Keep current files", "保留当前文件")}
        </button>
        <button type="button" onClick={() => resolve("restore_branch_code")}>
          {text("Restore branch code", "恢复分支代码")}
        </button>
        <small>{text("File edits paused", "文件修改已暂停")}</small>
      </div>
    </div>
  );
}

export const MessageList = memo(function MessageList({
  paintRows = true,
}: {
  paintRows?: boolean;
}) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const chatKey = useSessionStore((s) => s.activeChatKey);
  const ids = useMessageIds(sessionId);
  const [, setOrigTick] = useState(0);
  useEffect(() => {
    const fn = () => setOrigTick((n) => n + 1);
    originalsSubs.add(fn);
    return () => { originalsSubs.delete(fn); };
  }, []);
  const hiddenCovered = new Set<string>();
  const coveredSet = new Set<string>();
  const snap = useSessionStore.getState();
  for (const id of ids) {
    const m = snap.messagesById[id];
    if (!m) continue;
    if (m.kind !== "compaction" || m.slot !== "card" || !m.coversIds?.length) continue;
    for (const cid of m.coversIds) {
      coveredSet.add(cid);
      if (!originalsOpen.has(m.id)) hiddenCovered.add(cid);
    }
  }
  const runningTask = useSessionStore((s) =>
    sessionId ? s.runningTasks[sessionId] ?? null : null,
  );
  // Pin to #chatView (column, not the scroller). Portal into #chatArea
  // puts absolute-bottom on the scroll content, so the button is off
  // screen exactly when we want it visible.
  const jumpHost = typeof document !== "undefined"
    ? document.getElementById("chatView")
    : null;
  // Only the LAST row's role matters here (see ``showPending`` below).
  // Subscribing to the whole ``messagesById`` map would re-render this
  // component — and re-map every id — on every single streaming delta,
  // because ``updateMessage`` returns a fresh map object each time.
  const lastId = ids.length ? ids[ids.length - 1] : null;
  const pendingAnchor = runtimeState._pendingExpandAttach?.anchor;
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
    paintRows,
  );
  const [railTarget, setRailTarget] = useState<string | null>(null);
  const alwaysLive = collectAlwaysLive(
    ids,
    (id) => snap.messagesById[id],
    [pendingAnchor, railTarget],
  );
  const [view, setView] = useState({ top: 0, h: 800 });
  const measureGate = useRef(false);
  const [, setMeasureGen] = useState(0);
  const notifyMeasured = useCallback(() => {
    if (measureGate.current) return;
    measureGate.current = true;
    requestAnimationFrame(() => {
      measureGate.current = false;
      setMeasureGen((n) => n + 1);
    });
  }, []);
  useLayoutEffect(() => {
    if (!paintRows) return;
    const area = document.getElementById("chatArea");
    if (!area) return;
    const next = { top: area.scrollTop, h: area.clientHeight };
    setView((prev) => (prev.top === next.top && prev.h === next.h ? prev : next));
  }, [chatKey, ids.length, paintRows]);
  useEffect(() => {
    if (!paintRows) return;
    const area = document.getElementById("chatArea");
    if (!area) return;
    let raf = 0;
    const sync = () => {
      raf = 0;
      if (chatKey && area.clientWidth > 0 && noteChatWidth(chatKey, area.clientWidth)) {
        setMeasureGen((n) => n + 1);
      }
      const next = { top: area.scrollTop, h: area.clientHeight };
      setView((prev) => (prev.top === next.top && prev.h === next.h ? prev : next));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(sync);
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    const ro = new ResizeObserver(sync);
    ro.observe(area);
    return () => {
      area.removeEventListener("scroll", onScroll);
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [chatKey, paintRows]);
  const windowNodes: WindowNode[] = [];
  {
    let i = 0;
    while (i < ids.length) {
      const id = ids[i];
      if (coveredSet.has(id)) {
        const first = id;
        while (i < ids.length && coveredSet.has(ids[i])) i += 1;
        windowNodes.push({ kind: "fold", id: first });
      } else {
        windowNodes.push({ kind: "row", id });
        i += 1;
      }
    }
  }
  if (paintRows && chatKey && typeof document !== "undefined") {
    const areaW = document.getElementById("chatArea")?.clientWidth ?? 0;
    if (areaW > 0) noteChatWidth(chatKey, areaW);
  }
  const liveSet = paintRows && chatKey
    ? decideLiveRows({
        nodes: windowNodes,
        heights: heightsFor(chatKey),
        scrollTop: view.top,
        viewH: view.h,
        always: alwaysLive,
        listLen: ids.length,
        recycleMin: RECYCLE_MIN_ROWS,
      })
    : null;
  useLayoutEffect(() => {
    if (!paintRows || !chatKey || !liveSet) return;
    const root = document.getElementById("chatMessages");
    if (!root) return;
    let changed = false;
    root.querySelectorAll<HTMLElement>("[data-fold-id]").forEach((el) => {
      const fid = el.dataset.foldId;
      if (!fid) return;
      if (setRowHeight(chatKey, foldKey(fid), el.offsetHeight) !== "same") {
        changed = true;
      }
    });
    if (changed) notifyMeasured();
  });
  const [jumpShown, setJumpShown] = useState(false);
  const [jumpLeaving, setJumpLeaving] = useState(false);
  useEffect(() => {
    const want = paintRows && detached && ids.length > 0;
    if (want) {
      setJumpLeaving(false);
      setJumpShown(true);
      return;
    }
    if (!jumpShown) return;
    setJumpLeaving(true);
    const t = window.setTimeout(() => {
      setJumpShown(false);
      setJumpLeaving(false);
    }, JUMP_LATEST_FADE_MS);
    return () => window.clearTimeout(t);
  }, [detached, ids.length, jumpShown, paintRows]);

  // Parent-return actions: once the reload has rows on screen, reveal the
  // original sub-agent timeline row. Collapsed strips unmount their children,
  // so first open the exact strip indexed by branch head, then locate the row.
  // The flag is consumed only when the reveal actually runs, so a
  // mid-hydration re-render just reschedules it.
  useEffect(() => {
    if (!paintRows || !runtimeState._pendingExpandAttach || ids.length === 0) return;
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
  }, [ids, paintRows]);

  // Fade the transcript in once per session switch. The ref remembers
  // which session already faded, so streaming updates (ids.length
  // growing) inside the same session don't re-trigger the animation.
  const lastFadedSession = useRef<string | null>(null);
  useEffect(() => {
    if (!paintRows || !sessionId || ids.length === 0) return;
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
  }, [sessionId, ids.length, paintRows]);

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
      <WorkspaceAlignmentBanner sessionId={sessionId} />
      {paintRows ? (
        <MessageRail hiddenKey={[...hiddenCovered].join("\n")} onSeek={setRailTarget} />
      ) : null}
      {paintRows ? (() => {
        const nodes: ReactNode[] = [];
        let i = 0;
        while (i < ids.length) {
          const id = ids[i];
          if (coveredSet.has(id)) {
            const run: string[] = [];
            while (i < ids.length && coveredSet.has(ids[i])) {
              run.push(ids[i]);
              i += 1;
            }
            nodes.push(
              <div
                key={`${run[0]}_fold`}
                className="compaction-orig-fold"
                data-fold-id={run[0]}
                data-open={hiddenCovered.has(run[0]) ? "0" : "1"}
              >
                <div className="compaction-orig-fold-inner">
                  {run.map((cid) => (
                    <div key={cid} className="covered-turn">
                      <MessageRow id={cid} />
                    </div>
                  ))}
                </div>
              </div>,
            );
            continue;
          }
          nodes.push(
            liveSet && chatKey ? (
              <RecyclableRow
                key={id}
                id={id}
                chatKey={chatKey}
                live={liveSet.has(id)}
                onMeasured={notifyMeasured}
              />
            ) : (
              <div key={id} style={{ display: "contents" }}>
                <MessageRow id={id} />
              </div>
            ),
          );
          i += 1;
        }
        return nodes;
      })() : null}
      {paintRows && showPending ? (
        <PendingReplyIndicator timestamp={runningTask?.started_at} />
      ) : null}
      {paintRows ? <SelfUpdateHistory key={sessionId} sessionId={sessionId} /> : null}
      {/* Messages typed during the run — dimmed rows under the live
          turn, drained one at a time when it ends. */}
      {paintRows ? (
        <QueuedMessages sessionId={sessionId} onStopAndSend={stopAndSend} />
      ) : null}
      {paintRows && jumpShown && jumpHost
        ? createPortal(
            <div className={[
              "jump-latest-anchor",
              runningTask ? "is-live" : "",
              jumpLeaving ? "is-leaving" : "",
            ].filter(Boolean).join(" ")}>
              <button
                type="button"
                className="jump-latest"
                onClick={jumpToLatest}
                title={text("Jump to latest", "跳到最新")}
              >
                {runningTask ? (
                  <span className="jump-latest-live" aria-hidden>
                    <i />
                    <i />
                    <i />
                  </span>
                ) : (
                  <ArrowDown aria-hidden />
                )}
                {text("Jump to latest", "跳到最新")}
              </button>
            </div>,
            jumpHost,
          )
        : null}
    </>
  );
});
