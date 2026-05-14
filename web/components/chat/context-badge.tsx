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
import { buildUsageText } from "@/lib/format";
import { useSessionStore } from "@/lib/session-store";

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

  const usage = useSessionStore((s) => (sid ? s.tokens[sid] : undefined));
  const provider = useSessionStore((s) => s.agentSettings.chat?.provider);

  if (!sid || !usage) return null;

  const text = buildUsageText(
    {
      input_tokens: usage.input,
      output_tokens: usage.output,
      cache_read: usage.cache_read,
    },
    provider ?? null,
  );
  if (!text) return null;

  return (
    <span className="context-stats-label" title={text.tooltip}>
      {text.text}
    </span>
  );
}
