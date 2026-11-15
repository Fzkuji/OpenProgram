"use client";

/** The saved Agents, shared by everything that names or pictures one. */
import { useQuery } from "@tanstack/react-query";

import type { Agent } from "@/components/agents/agent-types";
import { jsonFetch } from "@/lib/net/fetch-client";

export const AGENTS_KEY = ["agents"] as const;

export function useAgents() {
  return useQuery({
    queryKey: AGENTS_KEY,
    queryFn: () => jsonFetch<{ agents: Agent[] }>("/api/agents").then((data) => data.agents),
  });
}
