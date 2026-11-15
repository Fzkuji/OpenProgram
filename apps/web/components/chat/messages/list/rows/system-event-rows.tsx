"use client";

/** System events in the transcript: the one-line event row and the
 *  compaction card, which folds the original messages open in place. */
import { useEffect, useRef, useState } from "react";
import { type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "../../bubbles/markdown";
import { formatMessageTime } from "@/lib/chat/transcript/message-time";

export const SYSTEM_EVENT_KINDS = new Set(["compaction", "snip", "event"]);

function formatEventTime(timestamp?: number): string {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
    return "";
  }
  const value = new Date(timestamp > 1e12 ? timestamp : timestamp * 1000);
  return formatMessageTime(value);
}

export const originalsOpen = new Set<string>();

export const originalsSubs = new Set<() => void>();

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

export function SystemEventRow({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  const n = msg.summarisedCount;
  const tb = msg.tokensBefore;
  const ta = msg.tokensAfter;
  const label = msg.kind === "compaction" && msg.slot === "event" && typeof n === "number"
    ? (tb != null && ta != null
      ? text(
          `Context compacted here: covered ${n} messages, estimated ${tb} → ${ta} tokens`,
          `此处压缩了上下文：盖住 ${n} 条，本地估算 ${tb} → ${ta} tokens`,
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

export function CompactionCard({ msg }: { msg: ChatMsg }) {
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
