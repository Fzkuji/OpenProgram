"use client";

// The pending inbox: reports from always-on conversations still waiting
// for the user (GET /api/always-on/pending). A decision stays until the
// user writes in its conversation; news stays until the conversation is
// opened. The sidebar's Pending row, the Pending page and the top of the
// Activity panel all read this one query.
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { jsonFetch } from "@/lib/net/fetch-client";
import { onConversationWorkChanged } from "@/lib/chat/conversation-work";
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
  /** A scheduled task's result: the tip of that task's branch. */
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

/** Read on mount, on every always-on change frame, on focus and every 30 seconds
 *  (a reply that answers a decision publishes no frame of its own). */
export function usePendingInbox(): { items: PendingItem[]; loaded: boolean } {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: PENDING_QUERY_KEY,
    queryFn: () => jsonFetch<{ items: PendingItem[] }>("/api/always-on/pending").then(body => body.items ?? []),
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
    staleTime: 5000,
  });
  useEffect(() => onConversationWorkChanged(() => {
    void client.invalidateQueries({ queryKey: PENDING_QUERY_KEY });
  }), [client]);
  return { items: query.data ?? [], loaded: query.data !== undefined };
}
