"use client";

/**
 * The conversation's branches as the Branches panel and the DAG know them
 * (`runtimeState._branchesByConv`): name, which one is checked out, and
 * each one's lane colour in the graph. The runtime fetches them when a
 * conversation loads; this re-reads them whenever it says they changed.
 * Sub-agents' own conversations and scheduled tasks' branches are left out.
 */
import { useEffect, useState } from "react";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { LANE_COLORS } from "@/lib/format-utils/lane-colors";

/** One row of `runtimeState._branchesByConv[sessionId]`. */
interface BranchRow {
  head_msg_id: string;
  branch_id?: string;
  name?: string;
  active?: boolean;
  updated_at?: number | null;
  /** A sub-agent's conversation kept in this session, not a branch the user made. */
  sub_agent?: boolean;
  /** A scheduled task's branch; its runs show on the task's page. */
  scheduled_task?: boolean;
}

export type ConversationBranch = { branchId?: string; head: string; name: string | null; active: boolean; color: string; updatedAt: number | null };

function read(sessionId: string | null): ConversationBranch[] {
  if (!sessionId) return [];
  const rows = (runtimeState._branchesByConv[sessionId] as BranchRow[] | undefined) || [];
  const colors = runtimeState._branchLaneColorMap as Record<string, string>;
  // A sub-agent's own conversation is reached through its main agent.
  return rows.map((row, index) => ({
    branchId: row.branch_id, head: row.head_msg_id, name: row.name || null, active: Boolean(row.active),
    color: colors[row.head_msg_id] || LANE_COLORS[index % LANE_COLORS.length],
    updatedAt: row.updated_at ?? null, subAgent: Boolean(row.sub_agent) || (Boolean(row.scheduled_task) && !row.active),
  })).filter(row => !row.subAgent).map(({ subAgent: _, ...row }) => row);
}

export function useConversationBranches(sessionId: string | null): ConversationBranch[] {
  const [branches, setBranches] = useState(() => read(sessionId));
  useEffect(() => {
    const update = () => setBranches(read(sessionId));
    update();
    window.addEventListener("branches-updated", update);
    return () => window.removeEventListener("branches-updated", update);
  }, [sessionId]);
  return branches;
}
