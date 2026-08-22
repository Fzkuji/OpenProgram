/**
 * Conversation-list store mirror.
 *
 * The bridge keeps a heavy `runtimeState.conversations` map (per-session
 * messages / graph / head_id used by the legacy DOM renderer). The React
 * sidebar reads from `store.conversations` (a ConvSummary map) instead, so
 * every place that mutates the sidebar-relevant fields of that map also
 * calls through here to keep `store.conversations` authoritative.
 *
 * These helpers call the zustand store directly; the store module is a plain
 * singleton, so there is no React mount-order dependency.
 */

import { useSessionStore, type ConvSummary } from "@/lib/session-store";

/** Pull only the ConvSummary fields off a (possibly heavy) legacy conv. */
function toSummary(c: Record<string, unknown>): ConvSummary {
  return {
    id: String(c.id),
    title: typeof c.title === "string" ? c.title : "",
    created_at: c.created_at as number | undefined,
    // Recency-sort key. The server sends it and the sidebar sorts on
    // `updated_at || created_at`; dropping it here made every reloaded row
    // fall back to created_at (0 for persisted rows) so a fresh session
    // sank to the bottom after refresh.
    updated_at: c.updated_at as number | undefined,
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
    workspace_alignment: (
      c.workspace_alignment as ConvSummary["workspace_alignment"]
    ) ?? undefined,
  };
}

/** Replace the whole conversation summary map. */
export function mirrorSetConvs(list: Record<string, unknown>[]): void {
  useSessionStore.getState().setConversations(list.map(toSummary));
}

/** Insert or update one conversation summary. */
export function mirrorUpsertConv(c: Record<string, unknown>): void {
  if (!c || !c.id) return;
  useSessionStore.getState().upsertConversation(toSummary(c));
}
