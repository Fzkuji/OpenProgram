"use client";

/**
 * Per-conversation token-usage badge for the Composer's bottom row.
 *
 * Replaces the legacy `#tokenBadge` DOM node (defined in
 * `public/html/index.html`) and the imperative render code in
 * `public/js/shared/providers.js` (`_renderTokenBadge` /
 * `refreshTokenBadge`). Both legacy paths now push the latest
 * `{input, output, cache_read}` tuple into the Zustand store via
 * `setContextStats`; this component is the sole renderer.
 *
 * Visual: a small progress ring (Claude Code style — 12px svg in a
 * 20px button, var(--border) track, var(--accent-orange) arc starting
 * at 12 o'clock). ALWAYS renders: a session with no usage yet (or no
 * session at all) shows the empty ring at 0 progress instead of
 * vanishing from the controls row. Tooltip carries the
 * "Context used / window (pct)" breakdown.
 */
import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSessionStore } from "@/lib/session-store";
import { ContextBreakdownPanel } from "./context-breakdown-panel";

interface ContextBadgeProps {
  /** Active conversation id. The component is keyed on this so a
   *  session switch immediately drops to "no data" until the new
   *  session's usage arrives. Accepts ``string | null`` (no session) and
   *  the legacy ``sessionId`` prop name preserved for the unmigrated
   *  `<ChatView>` call site. */
  sessionId?: string | null;
}

export function ContextBadge({ sessionId }: ContextBadgeProps) {
  // Resolve session: caller may pass `sessionId` (legacy ChatView path)
  // or omit it (Composer path) — in the latter case we fall back to the
  // store's current conversation id.
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const sid = sessionId ?? currentSessionId;

  // 点 badge 弹出 /context 分类分解面板（随时看当前会话 context 构成）。
  // open 状态放 store，好让 /context slash 命令也能切它。
  const panelOpen = useSessionStore((s) => s.contextPanelOpen);
  const setPanelOpen = useSessionStore((s) => s.setContextPanelOpen);
  // 当前分支头：切分支时 store.heads 会更新，弹窗据此重取该分支的上下文。
  const headId = useSessionStore((s) => (sid ? s.heads[sid] : undefined));

  // 圆环 DOM ref —— 用它的屏幕坐标把弹窗 portal 到 body 并 fixed 定位，
  // 彻底脱离输入框的圆角/overflow 裁切，真正置顶。
  const ringRef = useRef<HTMLButtonElement>(null);
  const [anchor, setAnchor] = useState<{ right: number; bottom: number } | null>(null);
  useLayoutEffect(() => {
    if (!panelOpen) return;
    const measure = () => {
      const el = ringRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      // 卡片右缘对齐圆环右缘；底缘抬到输入框可见边缘（圆环行顶再上
      // 10px band gap − 1px 外扩 ring = 9）——底部一排弹层统一压到
      // 输入框上，控件行保持可见。
      setAnchor({
        right: window.innerWidth - r.right,
        bottom: window.innerHeight - r.top + 9,
      });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [panelOpen]);

  const usage = useSessionStore((s) => (sid ? s.tokens[sid] : undefined));
  const ctxWindow = useSessionStore((s) => (sid ? s.contextWindow[sid] : undefined));
  const fallbackProvider = useSessionStore((s) => s.agentSettings.chat?.provider);
  const fallbackModel = useSessionStore((s) => s.agentSettings.chat?.model);

  // 永远渲染圆环：还没有 usage 的会话（或还没有会话）显示 0 进度的空环，
  // 而不是整个消失（“圆环不见了”就是以前 return null 造成的）。
  // tooltip: 详细 breakdown + 模型/provider 元信息. usage 里的 model 来自
  // backend context_stats 事件, 比 agentSettings 更精确 (单 turn 内可能
  // 切了 provider, agentSettings 是最终值).
  const modelLabel = usage?.model || fallbackModel || "";
  const providerLabel = usage?.provider || fallbackProvider || "";
  const metaLine = [providerLabel, modelLabel].filter(Boolean).join(" · ");

  // 用量百分比：input tokens / context window（拿不到 window 时给个保守默认）
  const win = ctxWindow && ctxWindow > 0 ? ctxWindow : 200_000;
  const used = usage?.input || 0;
  const pct = Math.max(0, Math.min(1, used / win));

  // tooltip 用 Claude Code 那种「Context 用了多少/共多少 (百分比)」格式
  const fmtNum = (n: number) =>
    n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
  const ringTooltip =
    `Context ${fmtNum(used)} / ${fmtNum(win)} (${(pct * 100).toFixed(0)}%)` +
    (metaLine ? ` · ${metaLine}` : "");

  // 环形进度（Claude Code 实测：12px svg、描边 2、轨道 var(--border)、
  // 进度 var(--accent-orange)、-90° 起点）
  const R = 5;               // 半径 — 12px viewBox 里留 1px 描边余量
  const SW = 2;              // 描边宽度
  const C = 2 * Math.PI * R; // 周长

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={ringRef}
        className="context-ring-badge"
        title={ringTooltip}
        onClick={() => setPanelOpen(!panelOpen)}
        aria-label="Context usage"
      >
        <svg width="12" height="12" viewBox="0 0 12 12">
          <circle
            cx="6"
            cy="6"
            r={R}
            fill="none"
            stroke="var(--border)"
            strokeWidth={SW}
          />
          <circle
            cx="6"
            cy="6"
            r={R}
            fill="none"
            stroke="var(--accent-orange)"
            strokeWidth={SW}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - pct)}
            transform="rotate(-90 6 6)"
          />
        </svg>
      </button>
      {panelOpen &&
        anchor &&
        typeof document !== "undefined" &&
        createPortal(
          <>
            {/* 透明全屏遮罩：点圆环外关闭（不压暗背景，纯 click-catcher）*/}
            <div
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 9998,
              }}
              onClick={() => setPanelOpen(false)}
            />
            {/* 浮动卡片：fixed 定位，右下角对齐圆环右下角 → 卡片盖住圆环，
                向上、向左展开。portal 到 body，彻底脱离输入框裁切并置顶。*/}
            <div
              style={{
                position: "fixed",
                right: anchor.right,
                bottom: anchor.bottom,
                zIndex: 9999,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <ContextBreakdownPanel
                sessionId={sid}
                headId={headId ?? null}
                onClose={() => setPanelOpen(false)}
              />
            </div>
          </>,
          document.body,
        )}
    </span>
  );
}
