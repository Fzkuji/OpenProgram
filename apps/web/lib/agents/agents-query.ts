"use client";

/** The saved Agents, shared by everything that names or pictures one. */
import { useQuery } from "@tanstack/react-query";

import type { Agent } from "@/components/agents/agent-types";
import { jsonFetch } from "@/lib/net/fetch-client";
import { sharedRead } from "@/lib/net/shared-get";
import { getQueryClient } from "@/lib/query-client";

export const AGENTS_KEY = ["agents"] as const;

/** The saved Agents; callers that ask while a request is out share its answer
 *  (the Agents page and the shell's list both ask as the page opens). */
export function readAgents(): Promise<Agent[]> {
  return sharedRead("/api/agents", () => jsonFetch<{ agents: Agent[] }>("/api/agents").then((data) => data.agents));
}

export function useAgents() {
  return useQuery({ queryKey: AGENTS_KEY, queryFn: readAgents });
}

/** The Agents page's first read, started on hover or at idle; a list already
 *  read is not asked for again until the Worker says an Agent changed
 *  (lib/query-client.ts). A failure only warns. */
export function prefetchAgents(): Promise<void> {
  const client = getQueryClient();
  if (!client) return Promise.resolve();
  return client.prefetchQuery({ queryKey: AGENTS_KEY, queryFn: readAgents })
    .catch((cause) => { console.warn("[prefetch] agents", cause); });
}
