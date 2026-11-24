"use client";

// The inbox: reports from always-on conversations and scheduled task runs
// still waiting for the user (GET /api/always-on/pending). A decision stays
// until the user writes in its conversation; news stays until the
// conversation is opened. The count on the sidebar's Tasks row, the Tasks
// page's Inbox tab and the top of the Activity panel all read this one query.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { jsonFetch } from "@/lib/net/fetch-client";
import { useChanged } from "@/lib/net/ws/changes";
import type { AlwaysOnReportKind } from "@/lib/agents/always-on";
import { openSession } from "@/lib/tabs/navigation/open-session";

export type PendingItem = {
  session_id: string;
  session_title: string;
  agent_id: string;
  agent_name: string;
  report_id: string;
  kind: AlwaysOnReportKind;
  title: string;
  body: string;
  at: number;
  /** Set when a scheduled task's run left the report, not the Agent. */
  task_id?: string | null;
  task_title?: string | null;
};

export const PENDING_QUERY_KEY = ["always-on-pending"] as const;

/** Decisions first, then news; each newest first (the server's order). */
export function groupPending(items: PendingItem[]): { decide: PendingItem[]; news: PendingItem[] } {
  return {
    decide: items.filter(item => item.kind === "approval"),
    news: items.filter(item => item.kind !== "approval"),
  };
}

/** One list: decisions first, then news. */
export function pendingInOrder(items: PendingItem[]): PendingItem[] {
  const { decide, news } = groupPending(items);
  return [...decide, ...news];
}

/** Open the conversation a report waits in. */
export function openPendingItem(item: PendingItem): void {
  openSession(item.session_id);
}

/** Read on first mount, then when the Worker says the inbox may have
 *  changed (`inbox`: a report written or seen, or a person wrote in a
 *  conversation, which can answer a decision; lib/net/ws/changes.ts). */
export function usePendingInbox(): { items: PendingItem[]; loaded: boolean } {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: PENDING_QUERY_KEY,
    queryFn: () => jsonFetch<{ items: PendingItem[] }>("/api/always-on/pending").then(body => body.items ?? []),
  });
  useChanged("inbox", () => client.invalidateQueries({ queryKey: PENDING_QUERY_KEY }));
  return { items: query.data ?? [], loaded: query.data !== undefined };
}
