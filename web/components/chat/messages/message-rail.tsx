"use client";

/**
 * 左缘消息导航（Codex 桌面端风格）。
 *
 * 聊天列左缘一列小横杠刻度，一条刻度 = 用户发过的一条消息；当前可视
 * 的那条加亮。整条 rail 接管指针：光标 Y 映射到最近的一条刻度（含缝隙），
 * 只有那一条突变加宽 + 加亮，跟手不滞后。同一条右侧弹出该条消息的
 * 预览卡（文本截断 + 附件 chips），绝不渲染全量列表。点击/Enter
 * 平滑滚到那条消息并闪一下。sticky 挂在滚动容器（#chatArea）里。
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { parseUserAttachments, UserAttachments } from "./user-attachments";

const EMPTY_ORDER: string[] = [];

const DASH_H = 2.5; // 刻度厚度 px
const BASE_W = 9; // 静息宽度 px
const MAX_EXTRA_W = 26; // 放大最多再加的宽度 px
const GAP = 10; // 刻度间距 px（固定，不随消息数压缩；超长时 rail 内部滚动）

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
  const bubble = (el.querySelector(".message-content") as HTMLElement) ?? el;
  const flash = () => {
    // 橙色背景闪烁一下（滚到位后触发）。
    bubble.classList.remove("rail-flash");
    void bubble.offsetWidth;
    bubble.classList.add("rail-flash");
    window.setTimeout(() => bubble.classList.remove("rail-flash"), 1400);
  };

  const area = document.getElementById("chatArea");
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  // 等平滑滚动停下再闪：优先 scrollend，兜底 700ms 超时。
  if (area) {
    let done = false;
    const fire = () => {
      if (done) return;
      done = true;
      area.removeEventListener("scrollend", fire);
      flash();
    };
    area.addEventListener("scrollend", fire, { once: true });
    window.setTimeout(fire, 700);
  } else {
    window.setTimeout(flash, 400);
  }
}

/** 单条消息的预览卡 — 只有悬停/聚焦那条才渲染（惰性解析附件）。 */
function PreviewCard({ content, top, left }: { content: string; top: number; left: number }) {
  const { attachments, text } = useMemo(
    () => parseUserAttachments(content),
    [content],
  );
  return (
    <div className="msg-rail-card" style={{ top, left }} role="presentation">
      {text ? <div className="msg-rail-card-text">{text}</div> : null}
      <UserAttachments items={attachments} />
    </div>
  );
}

export function MessageRail() {
  const msgs = useUserMessages();
  const [activeId, setActiveId] = useState<string | null>(null);
  // 光标最靠近的刻度索引；null = 鼠标不在条上。预览卡、加宽、加亮
  // 都锁到这同一条，保证"指哪条就是哪条"。
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const dashRefs = useRef<(HTMLButtonElement | null)[]>([]);

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
      // 已滚到底：强制高亮最后一条（最后一条可能很短、顶部还没越过探测线，
      // 否则会卡在倒数第二条）。
      if (area.scrollTop + area.clientHeight >= area.scrollHeight - 4) {
        setActiveId(msgs[msgs.length - 1]?.id ?? null);
        return;
      }
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

  // 以命中条为中心的直线坡度：命中条最长，向外每条线性递减，第 SPAN
  // 条归零（三角形凸包，非曲线）。基于离散 hoverIdx，跟手不滞后。
  const SPAN = 4; // 命中条向外第几条收回静息长度
  const widthAt = (i: number): number => {
    if (hoverIdx == null) return BASE_W;
    const d = Math.abs(i - hoverIdx);
    const t = Math.max(0, 1 - d / SPAN);
    return BASE_W + MAX_EXTRA_W * t;
  };

  // 光标 Y → 视觉上最近的那条：直接比每个刻度的真实中点，鼠标落在
  // 线的上/下空白也命中最近的一条（不受 padding / gap 公式误差影响）。
  const idxFromY = (clientY: number): number | null => {
    let best: number | null = null;
    let bestD = Infinity;
    for (let i = 0; i < dashRefs.current.length; i++) {
      const el = dashRefs.current[i];
      if (!el) continue;
      const r = el.getBoundingClientRect();
      const d = Math.abs(clientY - (r.top + r.height / 2));
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  };

  return (
    <div className="msg-rail-anchor">
      <div
        ref={railRef}
        className="msg-rail"
        style={{ gap: GAP }}
        onMouseMove={(e) => setHoverIdx(idxFromY(e.clientY))}
        onMouseLeave={() => setHoverIdx(null)}
        onClick={(e) => {
          const i = idxFromY(e.clientY);
          if (i != null && msgs[i]) scrollToMsg(msgs[i].id);
        }}
      >
        {msgs.map((m, i) => (
          <button
            key={m.id}
            ref={(el) => {
              dashRefs.current[i] = el;
            }}
            type="button"
            className={
              "msg-rail-dash" +
              (m.id === activeId ? " active" : "") +
              (i === hoverIdx ? " hovered" : "")
            }
            style={{ width: widthAt(i) }}
            aria-label={m.preview}
            onFocus={() => setHoverIdx(i)}
            onBlur={() => setHoverIdx((cur) => (cur === i ? null : cur))}
            onClick={(e) => {
              e.stopPropagation();
              scrollToMsg(m.id);
            }}
          />
        ))}
      </div>
      {hoverIdx != null && msgs[hoverIdx] ? (
        (() => {
          // 卡片是 rail 的兄弟（不被 rail 的 overflow 裁剪），绝对定位在
          // anchor 内。用命中条相对 anchor 的实时 rect 定位——rail 内部
          // 滚动后仍贴在命中那条右侧。
          const el = dashRefs.current[hoverIdx];
          const anchor = railRef.current?.parentElement;
          const ar = anchor?.getBoundingClientRect();
          const r = el?.getBoundingClientRect();
          const top = el && ar && r ? r.top - ar.top + r.height / 2 : 0;
          const rail = railRef.current?.getBoundingClientRect();
          // 贴在 rail 右缘外侧 8px。
          const left = rail && ar ? rail.right - ar.left + 8 : 0;
          return (
            <PreviewCard
              content={msgs[hoverIdx].content}
              top={top}
              left={left}
            />
          );
        })()
      ) : null}
    </div>
  );
}
