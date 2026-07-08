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
 * Visual: a `.context-stats-label` pill (rule lives in
 * `app/styles/05-chat.css`) showing the compact
 * "{tokens-in} in · {tokens-out} out" summary built by
 * `buildUsageText`. Tooltip carries the longer breakdown
 * (base / cache hit / out). Returns ``null`` — i.e. emits no DOM —
 * whenever the active session has no usage yet, matching the legacy
 * `:empty { display:none }` behavior with one fewer reflow.
 */
import { useState } from "react";
import { buildUsageText } from "@/lib/format-utils/format";
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

  // 点 badge 弹出 /context 分类分解面板（随时看当前会话 context 构成）
  const [panelOpen, setPanelOpen] = useState(false);

  const usage = useSessionStore((s) => (sid ? s.tokens[sid] : undefined));
  const ctxWindow = useSessionStore((s) => (sid ? s.contextWindow[sid] : undefined));
  const fallbackProvider = useSessionStore((s) => s.agentSettings.chat?.provider);
  const fallbackModel = useSessionStore((s) => s.agentSettings.chat?.model);

  if (!sid || !usage) return null;

  const text = buildUsageText(
    {
      input_tokens: usage.input,
      output_tokens: usage.output,
      cache_read: usage.cache_read,
      cache_create: usage.cache_create,
    },
    usage.provider ?? fallbackProvider ?? null,
  );
  if (!text) return null;

  // tooltip: 详细 breakdown + 模型/provider 元信息. usage 里的 model 来自
  // backend context_stats 事件, 比 agentSettings 更精确 (单 turn 内可能
  // 切了 provider, agentSettings 是最终值).
  const modelLabel = usage.model || fallbackModel || "";
  const providerLabel = usage.provider || fallbackProvider || "";
  const metaLine = [providerLabel, modelLabel].filter(Boolean).join(" · ");

  // 用量百分比：input tokens / context window（拿不到 window 时给个保守默认）
  const win = ctxWindow && ctxWindow > 0 ? ctxWindow : 200_000;
  const used = usage.input || 0;
  const pct = Math.max(0, Math.min(1, used / win));

  // tooltip 用 Claude Code 那种「Context 用了多少/共多少 (百分比)」格式
  const fmtNum = (n: number) =>
    n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
  const ringTooltip =
    `Context ${fmtNum(used)} / ${fmtNum(win)} (${(pct * 100).toFixed(0)}%)` +
    (metaLine ? ` · ${metaLine}` : "");

  // 环形进度（对齐 Claude Code 那种小圆环）
  const R = 8;               // 半径（描边变粗后收一点，避免超出 22 box）
  const SW = 3.5;            // 描边宽度（加粗 2.5 -> 3.5）
  const C = 2 * Math.PI * R; // 周长
  // 颜色按用量：够用蓝、偏满黄、快满红（对齐 Claude Code）
  const ringColor =
    pct >= 0.9 ? "#e5534b" : pct >= 0.7 ? "#e0a33c" : "#3b9eff";

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        className="context-ring-badge"
        title={ringTooltip}
        onClick={() => setPanelOpen((v) => !v)}
        aria-label="Context usage"
      >
        <svg width="22" height="22" viewBox="0 0 22 22">
          <circle
            cx="11"
            cy="11"
            r={R}
            fill="none"
            stroke="var(--border)"
            strokeWidth={SW}
          />
          <circle
            cx="11"
            cy="11"
            r={R}
            fill="none"
            stroke={ringColor}
            strokeWidth={SW}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - pct)}
            transform="rotate(-90 11 11)"
          />
        </svg>
      </button>
      {panelOpen && (
        <>
          {/* 透明全屏遮罩：点圆环外关闭（不压暗背景，纯 click-catcher）*/}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setPanelOpen(false)}
          />
          {/* 浮动卡片：锚定圆环，向上、向左展开 */}
          <div
            className="absolute z-50"
            style={{ bottom: "calc(100% + 8px)", right: 0 }}
            onClick={(e) => e.stopPropagation()}
          >
            <ContextBreakdownPanel
              sessionId={sid}
              onClose={() => setPanelOpen(false)}
            />
          </div>
        </>
      )}
    </span>
  );
}
