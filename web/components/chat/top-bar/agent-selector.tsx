"use client";

/**
 * Agent selector — the content of the chat / exec `<AgentBadge />`
 * popover.
 *
 * Lists every model the user enabled in Settings, grouped by provider,
 * and on pick writes the agent's default via `/api/agent_settings`
 * (and, for the chat agent on an active conversation, also pins it on
 * that conv via `/api/model` — the per-conv override otherwise wins
 * and the pick would silently no-op).
 *
 * Positioning / click-outside / portal are handled by the shadcn
 * <Popover> in `index.tsx`.
 */

import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { api } from "@/lib/api";
import { useTranslation } from "@/lib/i18n";
import { CHECK, GROUP_LABEL, MENU_PANEL, itemCls } from "./menu-styles";
import { Check } from "lucide-react";

export function AgentSelector({
  kind,
  currentProvider,
  currentModel,
  onClose,
}: {
  kind: "chat" | "exec";
  currentProvider?: string;
  currentModel?: string;
  onClose: () => void;
}) {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const { t } = useTranslation();
  const { data: models } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });

  async function pick(provider: string, model: string) {
    onClose();
    try {
      await api.setAgentSettings({ [kind]: { provider, model } });
      // The agent-settings write only sets the agent DEFAULT. The
      // active conversation has a per-conv provider/model override
      // that takes priority, so the chat pick must also go through
      // `/api/model` or it has zero effect on the current chat.
      if (kind === "chat" && currentSessionId) {
        await api.switchModel(provider, model, currentSessionId);
      }
    } catch (e) {
      alert(t("agent.switch_failed") + String(e));
    }
  }

  // Group enabled models by provider, preserving first-seen order.
  const byProvider: { provider: string; models: typeof models }[] = [];
  for (const m of models ?? []) {
    let group = byProvider.find((g) => g.provider === m.provider);
    if (!group) {
      group = { provider: m.provider, models: [] };
      byProvider.push(group);
    }
    group.models!.push(m);
  }

  return (
    <div className={`${MENU_PANEL} w-[300px]`}>
      {(models ?? []).length === 0 ? (
        <div className="px-[8px] py-[6px] text-[12px] text-text-muted">
          {t("agent.no_enabled_models")}{" "}
          <a href="/settings" className="text-[var(--accent-blue)] no-underline">
            {t("agent.enable_models")} →
          </a>
        </div>
      ) : (
        byProvider.map((group) => (
          <div key={group.provider}>
            <div className={GROUP_LABEL}>{group.provider}</div>
            {group.models!.map((m) => {
              const active =
                currentProvider === m.provider &&
                (currentModel === m.id ||
                  currentModel === `${m.provider}:${m.id}`);
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => pick(m.provider, m.id)}
                  className={`${itemCls(active)} w-full text-left`}
                >
                  <span className="flex-1 truncate">{m.name}</span>
                  <CapIcons caps={m.capabilities} />
                  {m.context ? (
                    <span className="shrink-0 font-mono text-[11px] text-text-muted">
                      {fmtCtx(m.context)}
                    </span>
                  ) : null}
                  {active ? <Check size={14} className={CHECK} /> : null}
                </button>
              );
            })}
          </div>
        ))
      )}

      <div className="px-[8px] pb-[3px] pt-[5px] text-[11px]">
        <a href="/settings" className="text-[var(--accent-blue)] no-underline">
          {t("agent.manage_models")} →
        </a>
      </div>
    </div>
  );
}

/** Capability icons — vision / video / tools / reasoning. `ctx` is
 *  dropped here (the context window already shows as a number). Same
 *  glyph set the legacy `_CAP_ICONS` used in providers.js. */
function CapIcons({ caps }: { caps: string[] }) {
  const shown = caps.filter((c) => c !== "ctx");
  if (shown.length === 0) return null;
  return (
    <span className="flex shrink-0 items-center gap-[5px] text-text-muted">
      {shown.map((c) => {
        const icon = CAP_ICON[c];
        return icon ? (
          <span key={c} title={c[0].toUpperCase() + c.slice(1)}>
            {icon}
          </span>
        ) : null;
      })}
    </span>
  );
}

const CAP_ICON: Record<string, React.ReactNode> = {
  vision: (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  video: (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="2" y="5" width="20" height="14" rx="2" />
      <path d="m10 9 5 3-5 3z" />
    </svg>
  ),
  tools: (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
  reasoning: (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .962L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
      <path d="M20 3v4" />
      <path d="M22 5h-4" />
      <path d="M4 17v2" />
      <path d="M5 18H3" />
    </svg>
  ),
};

/** Compact context-window label: 200000 → "200k", 1048576 → "1M". */
function fmtCtx(n: number): string {
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}
