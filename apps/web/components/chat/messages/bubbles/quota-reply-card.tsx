"use client";

/**
 * The failed-reply card when a quota is spent
 * (docs/reference/design/providers/provider-management.html#chat):
 *
 *   OpenAI Codex 5h limit reached
 *   Resets 14:30 · Week 71% left
 *   [◎ Continue with deepseek-v4]  [Other model…]  [Wait]
 *
 * A spent balance reads "Kimi has no credit" with Top up first. The offer
 * comes from the Worker (`GET /api/providers/fallback`); nothing switches by
 * itself: a button pins the model on this pane's branch and sends the turn
 * again there (lib/models/fallback.ts). Wait keeps the model and folds the
 * buttons away. The raw error stays under the card's collapsed details.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, ExternalLink, KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";
import { showToast } from "@/lib/format-utils/toast";
import { cn } from "@/lib/utils";
import { bareModel, continueWith, fetchFallback } from "@/lib/models/fallback";
import {
  PICKER_QUERY_KEY,
  fixLink,
  isNoCredit,
  quotaCardText,
  type FailureCause,
} from "@/lib/models/provider-state";
import type { FallbackModel } from "@/lib/settings/provider-status.generated";
import { ModelMark } from "../../top-bar/chips/model-mark";
import { AgentSelector } from "../../top-bar/menus/agent-selector";

/** The whole card: icon, title and reset line, the raw error (`children`,
 *  the bubble's collapsed details), the buttons, then `after`. */
export function QuotaErrorCard({ msg, cause, sessionId, embedded, after, children }: {
  msg: ChatMsg;
  cause: FailureCause;
  sessionId: string | undefined;
  embedded?: boolean;
  after?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="turn-error" role="status" data-quota-card>
      <span className="turn-error-icon"><QuotaIcon cause={cause} /></span>
      <div className="turn-error-body">
        <QuotaCardHead cause={cause} />
        {children}
        {embedded ? null : <QuotaActions msg={msg} cause={cause} sessionId={sessionId} />}
        {after}
      </div>
    </div>
  );
}

/** The picker's read, for the provider's name, state and balance. */
function usePicker() {
  return useQuery({ queryKey: PICKER_QUERY_KEY, queryFn: api.listPickerModels }).data;
}

export function QuotaIcon({ cause }: { cause: FailureCause }) {
  const picker = usePicker();
  const state = cause.provider ? picker?.providers[cause.provider] : undefined;
  return isNoCredit(cause, state)
    ? <KeyRound size={20} aria-hidden="true" />
    : <Clock size={20} aria-hidden="true" style={{ color: "var(--warning-soft)" }} />;
}

/** Title and the reset line. */
export function QuotaCardHead({ cause }: { cause: FailureCause }) {
  const { text } = useTranslation();
  const picker = usePicker();
  const provider = cause.provider ?? "";
  const label = picker?.models.find((m) => m.provider === provider)?.providerLabel || provider || text("This provider", "该服务商");
  const { title, detail } = quotaCardText(cause, label, text, { picker: picker?.providers[provider] });
  return (
    <>
      <div className="turn-error-title" data-quota-title>{title}</div>
      {detail ? <div className="text-fs-xs text-text-secondary" data-quota-detail>{detail}</div> : null}
    </>
  );
}

/** Continue with the offer, Other model…, Wait (or Top up for a balance). */
export function QuotaActions({ msg, cause, sessionId }: { msg: ChatMsg; cause: FailureCause; sessionId: string | undefined }) {
  const { text } = useTranslation();
  const picker = usePicker();
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const chat = useSessionStore((s) => (sessionId ? s.agentSettingsBySession[sessionId]?.chat : undefined));
  const contextTokens = useSessionStore((s) => (sessionId ? s.tokens[sessionId]?.total_used ?? 0 : 0));
  const provider = cause.provider || chat?.provider || "";
  const model = bareModel(chat?.provider, chat?.model);
  const { data: offer } = useQuery({
    queryKey: ["provider-fallback", provider, model, Math.round(contextTokens / 1000)],
    queryFn: () => fetchFallback(provider, model, contextTokens),
    enabled: !!sessionId && !!provider && !!model && !waiting,
    staleTime: 60_000,
  });
  if (!sessionId || waiting) return null;
  const state = picker?.providers[provider];
  const noCredit = isNoCredit(cause, state);
  const topUp = noCredit && state ? fixLink(state) : null;

  async function go(choice: { provider: string; model: string }) {
    if (!sessionId || busy) return;
    setBusy(true);
    try {
      await continueWith(sessionId, msg.id, choice.provider, choice.model);
    } catch (error) {
      showToast(text("Could not send again: ", "未能重新发送：") + String(error), { tone: "error" });
      setBusy(false);
    }
  }

  const suggestion = offer?.suggestion ?? null;
  const alternatives = offer?.alternatives ?? [];
  return (
    <div className="mt-1 flex flex-wrap gap-1.5" data-quota-actions>
      {topUp?.external ? (
        <Button asChild variant="elevated" size="xs">
          <a href={topUp.href} target="_blank" rel="noreferrer" className="no-underline" data-quota-top-up>
            {text("Top up", "充值")}
            <ExternalLink aria-hidden="true" />
          </a>
        </Button>
      ) : null}
      {suggestion ? (
        <Button
          variant="elevated"
          size="xs"
          disabled={busy}
          onClick={() => void go(suggestion)}
          data-quota-continue={`${suggestion.provider}:${suggestion.model}`}
          data-tip={suggestion.provider_label}
        >
          <ModelMark provider={suggestion.provider} size={14} />
          {text(`Continue with ${suggestion.name}`, `改用 ${suggestion.name} 继续`)}
        </Button>
      ) : null}
      <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="xs" disabled={busy} data-quota-other>
            {text("Other model…", "其他模型…")}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" sideOffset={6} className={cn(MENU_PANEL, "text-text-primary")}>
          {alternatives.length ? (
            <>
              <div className={GROUP_LABEL}>{text("Has room", "还有额度")}</div>
              {alternatives.map((alt) => (
                <Alternative key={`${alt.provider}:${alt.model}`} alt={alt} onPick={() => { setOpen(false); void go(alt); }} />
              ))}
              <div className={MENU_SEPARATOR} />
            </>
          ) : null}
          <AgentSelector
            currentProvider={chat?.provider}
            currentModel={chat?.model}
            onClose={() => setOpen(false)}
            onPick={(p, m) => void go({ provider: p, model: m })}
          />
        </DropdownMenuContent>
      </DropdownMenu>
      {noCredit ? null : (
        <Button
          variant="ghost"
          size="xs"
          disabled={busy}
          onClick={() => setWaiting(true)}
          data-quota-wait
          data-tip={text("Keep this model and send again after the reset", "保留当前模型，重置后再发送")}
        >
          {text("Wait", "等待")}
        </Button>
      )}
    </div>
  );
}

function Alternative({ alt, onPick }: { alt: FallbackModel; onPick: () => void }) {
  return (
    <DropdownMenuItem onSelect={onPick} className={cn(itemCls(false), MENU_ITEM_STATES)}>
      <ModelMark provider={alt.provider} />
      <span className="min-w-0 flex-1 truncate">{alt.name}</span>
      <span className="shrink-0 pl-4 text-fs-xs text-text-muted">{alt.provider_label}</span>
    </DropdownMenuItem>
  );
}
