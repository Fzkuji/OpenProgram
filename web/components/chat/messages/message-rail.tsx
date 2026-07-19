"use client";

/**
 * 左缘消息导航（Codex 桌面端风格）。
 *
 * 聊天列左缘一列小横杠刻度，一条刻度 = 用户发过的一条消息；当前可视
 * 的那条加亮。鼠标在导航条上移动时做 macOS Dock 式邻近放大：离光标
 * 越近的刻度越宽，按高斯衰减（半径约 2.5 个刻度），120ms 过渡；
 * reduced-motion 下不放大。悬停某个刻度只在其右侧弹出该条消息的
 * 预览卡（文本截断 + 附件 chips），绝不渲染全量列表。点击/Enter
 * 平滑滚到那条消息并闪一下。sticky 挂在滚动容器（#chatArea）里。
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { parseUserAttachments, UserAttachments } from "./user-attachments";

const EMPTY_ORDER: string[] = [];

const DASH_H = 3; // 刻度厚度 px
const BASE_W = 14; // 静息宽度 px
const MAX_EXTRA_W = 14; // 放大最多再加的宽度 px
const MAX_GAP = 7; // 刻度间距上限 px

function useUserMessages(): Array<{ id: string; content: string; preview: string }> {
  // 订阅稳定引用（selector 里造新数组会无限重渲染，React #185），
  // 派生列表用 useMemo。
  const sid = useSessionStore((s) => s.currentSessionId);
  const order = useSessionStore((s) =>
    (sid ? s.messageOrder[sid] : undefined) || EMPTY_ORDER);
  const byId = useSessionStore((s) => s.messagesById);
  return useMemo(() => {
    const out: Array<{ id: string; content: string; preview: string }> = [];
    for (const id of order) {
      const m = byId[id];
      if (!m || m.role !== "user") continue;
      if (m.display === "runtime") continue;
      const preview = (m.content || "").replace(/\s+/g, " ").trim();
      if (!preview) continue;
      out.push({ id, content: m.content || "", preview: preview.slice(0, 60) });
    }
    return out;
  }, [order, byId]);
}

function scrollToMsg(id: string): void {
  const container = document.getElementById("chatMessages");
  if (!container) return;
  const esc = window.CSS && CSS.escape ? CSS.escape(id) : id;
  const el = container.querySelector(`[data-msg-id="${esc}"]`) as HTMLElement | null;
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("dag-flash");
  void el.offsetWidth;
  el.classList.add("dag-flash");
  window.setTimeout(() => el.classList.remove("dag-flash"), 1400);
}

/** 单条消息的预览卡 — 只有悬停/聚焦那条才渲染（惰性解析附件）。 */
function PreviewCard({ content, top }: { content: string; top: number }) {
  const { attachments, text } = useMemo(
    () => parseUserAttachments(content),
    [content],
  );
  return (
    <div className="msg-rail-card" style={{ top }} role="presentation">
      {text ? <div className="msg-rail-card-text">{text}</div> : null}
      <UserAttachments items={attachments} />
    </div>
  );
}

export function MessageRail() {
  const msgs = useUserMessages();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  // 光标在导航条内的 Y 坐标；null = 鼠标不在条上（不放大）。
  const [cursorY, setCursorY] = useState<number | null>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [viewH, setViewH] = useState(600);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    const onResize = () => setViewH(window.innerHeight);
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      mq.removeEventListener("change", sync);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  // 当前可视消息跟随：滚动时取视口上缘 40% 处之前最近的一条用户消息。
  useEffect(() => {
    const area = document.getElementById("chatArea");
    const container = document.getElementById("chatMessages");
    if (!area || !container || msgs.length === 0) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const areaRect = area.getBoundingClientRect();
      const probe = areaRect.top + areaRect.height * 0.4;
      let current: string | null = msgs[0]?.id ?? null;
      for (const m of msgs) {
        const esc = window.CSS && CSS.escape ? CSS.escape(m.id) : m.id;
        const el = container.querySelector(`[data-msg-id="${esc}"]`);
        if (!el) continue;
        if ((el as HTMLElement).getBoundingClientRect().top <= probe) {
          current = m.id;
        } else break;
      }
      setActiveId(current);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    area.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      area.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [msgs]);

  if (msgs.length < 2) return null;

  // 刻度总高超出可视区时按可用高度均匀压缩间距（不出内部滚动条）。
  const maxH = viewH * 0.6;
  const n = msgs.length;
  const gap = Math.max(
    1,
    Math.min(MAX_GAP, (maxH - n * DASH_H) / Math.max(1, n - 1)),
  );
  const pitch = DASH_H + gap;

  // Dock 式邻近放大：以光标为中心的高斯衰减，sigma ≈ 2.5 个刻度。
  const sigma = 2.5 * pitch;
  const widthAt = (i: number): number => {
    if (cursorY == null || reducedMotion) return BASE_W;
    const center = i * pitch + DASH_H / 2;
    const d = cursorY - center;
    return BASE_W + MAX_EXTRA_W * Math.exp(-(d * d) / (2 * sigma * sigma));
  };

  return (
    <div className="msg-rail-anchor">
      <div
        ref={railRef}
        className="msg-rail"
        style={{ gap }}
        onMouseMove={(e) => {
          const rect = railRef.current?.getBoundingClientRect();
          if (rect) setCursorY(e.clientY - rect.top);
        }}
        onMouseLeave={() => {
          setCursorY(null);
          setHoverIdx(null);
        }}
      >
        {msgs.map((m, i) => (
          <button
            key={m.id}
            type="button"
            className={"msg-rail-dash" + (m.id === activeId ? " active" : "")}
            style={{ width: widthAt(i) }}
            aria-label={m.preview}
            onMouseEnter={() => setHoverIdx(i)}
            onFocus={() => setHoverIdx(i)}
            onBlur={() => setHoverIdx((cur) => (cur === i ? null : cur))}
            onClick={() => scrollToMsg(m.id)}
          />
        ))}
        {hoverIdx != null && msgs[hoverIdx] ? (
          <PreviewCard
            content={msgs[hoverIdx].content}
            top={hoverIdx * pitch + DASH_H / 2}
          />
        ) : null}
      </div>
    </div>
  );
}
