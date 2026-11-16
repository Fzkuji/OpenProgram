"use client";

/**
 * The keys of a provider in the model menu
 * (docs/reference/design/providers/provider-management.html#chat), inline
 * under the provider's models so no third panel opens over the first:
 * "This conversation uses <name>", then Automatic (the
 * provider's order), each credential with its last four or why it cannot
 * be used now, and Manage keys… on the provider's page (a client-side
 * route change; with Cmd, Ctrl or Shift it opens in a new tab and this
 * page stays). The check on Automatic or on a row says whether it is
 * pinned. Picking one pins this conversation to it
 * (`POST /api/sessions/{sid}/credentials`); Automatic clears the pin.
 * Shown for a provider with two or more credentials in a saved conversation.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { credentialName, credentialWord, readCredentials, strategyLabel, usableNow } from "@/components/settings/providers/detail/credential-model";
import type { SessionCredentials } from "@/components/settings/providers/types";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { providerPage } from "@/lib/models/provider-state";
import { navigate } from "@/lib/nav/navigate";
import { cn } from "@/lib/utils";

/** A plain click on a link changes the route in the page; one with Cmd,
 *  Ctrl or Shift is left to the browser (a new tab or window), and the page
 *  stays where it is. */
export function openInPage(
  e: { metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; preventDefault(): void },
  href: string, go: (href: string) => void,
): void {
  if (e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  go(href);
}

export const sessionCredentialsKey = (sid: string) => ["session-credentials", sid] as const;
export const providerCredentialsKey = (provider: string) => ["provider-credentials", provider] as const;

async function readSession(sid: string): Promise<SessionCredentials> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}/credentials`);
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as SessionCredentials;
}

export function ProviderKeys({ provider, sessionId, onClose }: {
  provider: string;
  sessionId: string | null;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  const queryClient = useQueryClient();
  const { data: list } = useQuery({
    queryKey: providerCredentialsKey(provider),
    queryFn: () => readCredentials(provider),
    staleTime: 30_000,
  });
  const { data: session } = useQuery({
    queryKey: sessionCredentialsKey(sessionId ?? ""),
    queryFn: () => readSession(sessionId!),
    enabled: !!sessionId,
    staleTime: 30_000,
  });
  const rows = list?.credentials ?? [];
  if (!sessionId || rows.length < 2 || !list) return null;

  const now = Date.now() / 1000;
  const pin = session?.pin[provider] ?? null;
  const using = pin ?? session?.binding[provider] ?? list.active;
  const current = rows.find((r) => r.id === using);
  const manage = providerPage(provider);

  async function choose(credential: string | null) {
    onClose();
    try {
      const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId!)}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, credential_id: credential }),
      });
      if (!r.ok) throw new Error(String(r.status));
      queryClient.setQueryData(sessionCredentialsKey(sessionId!), (await r.json()) as SessionCredentials);
    } catch {
      showToast(text("Could not change the key for this conversation.", "无法更改这个对话使用的密钥。"), { tone: "error" });
    }
  }

  return (
    <div data-picker-keys={provider} data-current-key={current?.id}>
      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      <div className={cn(GROUP_LABEL, "truncate")}>
        {text("This conversation uses", "这个对话使用")}{current ? ` ${credentialName(current, text)}` : ""}
      </div>
      <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => void choose(null)} data-credential-choice="auto">
        <MenuRow label={text(`Automatic (${strategyLabel(list.strategy, text).toLowerCase()})`, `自动（${strategyLabel(list.strategy, text)}）`)} checked={!pin} />
      </DropdownMenuItem>
      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      {rows.map((row) => {
        const word = credentialWord(row, now, text);
        return (
          <DropdownMenuItem
            key={row.id}
            className={cn(itemCls(false), MENU_ITEM_STATES)}
            disabled={!usableNow(row, now) && pin !== row.id}
            onSelect={() => void choose(row.id)}
            data-credential-choice={row.id}
          >
            <MenuRow
              label={credentialName(row, text)}
              trail={word ? <span className={cn("text-text-muted", usableNow(row, now) && row.key_tail && "font-mono")}>{word}</span> : undefined}
              checked={pin === row.id}
            />
          </DropdownMenuItem>
        );
      })}
      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")} onSelect={onClose}>
        {/* A plain click (or Enter) changes the route in the page; with a
            modifier the browser opens the href in a new tab and this page stays. */}
        <a href={manage} data-picker-manage-keys
          onClick={(e) => openInPage(e, manage, navigate)}>
          <MenuRow glyph="key-minimalistic" label={text("Manage keys…", "管理密钥…")} />
        </a>
      </DropdownMenuItem>
    </div>
  );
}
