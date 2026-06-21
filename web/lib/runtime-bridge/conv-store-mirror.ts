/**
 * Conversation-list store mirror.
 *
 * The legacy bridge keeps a heavy `window.conversations` map (per-session
 * messages / graph / head_id used by the legacy DOM renderer). The React
 * sidebar reads from `store.conversations` (a ConvSummary map) instead, so
 * every place that mutates the sidebar-relevant fields of `window.conversations`
 * also calls through here to keep `store.conversations` authoritative.
 *
 * These helpers go through `window.__sessionStore` (set by AppShell) so this
 * module has no import-time dependency on React mount order — they're no-ops
 * until the store is exposed (legacy-only page), matching the rest of the
 * bridge.
 */

import type { ConvSummary } from "@/lib/session-store";

interface ConvStoreApi {
  conversations: Record<string, ConvSummary>;
  setConversations: (list: ConvSummary[]) => void;
  upsertConversation: (c: ConvSummary) => void;
}

function store(): ConvStoreApi | null {
  const w = window as unknown as {
    __sessionStore?: { getState: () => Partial<ConvStoreApi> };
  };
  const s = w.__sessionStore?.getState();
  if (!s || !s.upsertConversation) return null;
  return s as ConvStoreApi;
}

/** Pull only the ConvSummary fields off a (possibly heavy) legacy conv. */
function toSummary(c: Record<string, unknown>): ConvSummary {
  return {
    id: String(c.id),
    title: typeof c.title === "string" ? c.title : "",
    created_at: c.created_at as number | undefined,
    agent_id: (c.agent_id as string | undefined) ?? undefined,
    source: (c.source as string | undefined) ?? undefined,
    peer_display: (c.peer_display as string | undefined) ?? undefined,
    channel: (c.channel as string | undefined) ?? undefined,
    account_id: (c.account_id as string | undefined) ?? undefined,
    peer: (c.peer as string | undefined) ?? undefined,
    preview: (c.preview as string | null | undefined) ?? null,
    pinned: !!c.pinned,
    archived: !!c.archived,
    group: (c.group as string | undefined) ?? "",
    status: (c.status as string | undefined) ?? undefined,
    unread: !!c.unread,
    project: (c.project as string | undefined) ?? "",
  };
}

/** Replace the whole conversation summary map. */
export function mirrorSetConvs(list: Record<string, unknown>[]): void {
  store()?.setConversations(list.map(toSummary));
}

/** Insert or update one conversation summary. */
export function mirrorUpsertConv(c: Record<string, unknown>): void {
  if (!c || !c.id) return;
  store()?.upsertConversation(toSummary(c));
}
