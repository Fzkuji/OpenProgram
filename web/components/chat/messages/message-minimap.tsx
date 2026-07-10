"use client";

/**
 * 右缘消息导航 — docs/design/ui/chat-turn-visual-spec.html 场景 3。
 *
 * 聊天列右缘一列小横线，一条横线 = 用户发过的一条消息；当前可视的
 * 那条加亮加长。悬停整列展开消息列表面板（单行截断），点击平滑滚到
 * 那条消息并闪一下。sticky 挂在滚动容器（#chatArea）里，不随内容
 * 滚走。
 */
import { useEffect, useMemo, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

const EMPTY_ORDER: string[] = [];

function useUserMessages(): Array<{ id: string; preview: string }> {
  // 订阅稳定引用（selector 里造新数组会无限重渲染，React #185），
  // 派生列表用 useMemo。
  const sid = useSessionStore((s) => s.currentSessionId);
  const order = useSessionStore((s) =>
    (sid ? s.messageOrder[sid] : undefined) || EMPTY_ORDER);
  const byId = useSessionStore((s) => s.messagesById);
  return useMemo(() => {
    const out: Array<{ id: string; preview: string }> = [];
    for (const id of order) {
      const m = byId[id];
      if (!m || m.role !== "user") continue;
      if (m.display === "runtime") continue;
      const preview = (m.content || "").replace(/\s+/g, " ").trim();
      if (!preview) continue;
      out.push({ id, preview: preview.slice(0, 60) });
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

export function MessageMinimap() {
  const msgs = useUserMessages();
  const [activeId, setActiveId] = useState<string | null>(null);

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

  return (
    <div className="msg-minimap-anchor" aria-hidden={msgs.length === 0}>
      <div className="msg-minimap">
        {msgs.map((m) => (
          <span
            key={m.id}
            className={"msg-minimap-dash" + (m.id === activeId ? " active" : "")}
          />
        ))}
        <div className="msg-minimap-panel">
          {msgs.map((m) => (
            <div
              key={m.id}
              className={"msg-minimap-row" + (m.id === activeId ? " active" : "")}
              onClick={() => scrollToMsg(m.id)}
              title={m.preview}
            >
              {m.preview}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
