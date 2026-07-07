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
  const tooltip = metaLine
    ? `${text.tooltip}\n${metaLine}`
    : text.tooltip;

  return (
    <>
      <span
        className="context-stats-label"
        title={tooltip}
        style={{ cursor: "pointer" }}
        onClick={() => setPanelOpen(true)}
      >
        {text.text}
      </span>
      {panelOpen && (
        <div
          className="fixed inset-0 z-50 flex justify-end"
          style={{ background: "rgba(0,0,0,0.3)" }}
          onClick={() => setPanelOpen(false)}
        >
          <div onClick={(e) => e.stopPropagation()}>
            <ContextBreakdownPanel
              sessionId={sid}
              onClose={() => setPanelOpen(false)}
            />
          </div>
        </div>
      )}
    </>
  );
}
