/** What Activity's Agents rows add to the executions the page already
 *  holds: each helper's status and usage, and the messages this
 *  conversation sent that nobody has answered yet. */
import { jsonFetch } from "../fetch-client";

export interface RosterFacts {
  /** running, needs approval, needs input, stalled, idle or failed. */
  agent_status: string | null;
  /** Admitted but still waiting for a free slot. */
  queued: boolean;
  /** Tokens and cost of the helper and every helper under it; its own
   *  tool calls. A value the server cannot give is null. */
  usage: { tokens: number | null; cost_usd: string | null; tool_calls: number | null } | null;
}

export interface OutgoingSend {
  kind: "outgoing";
  job_id: string;
  sender: string;
  to_session: string;
  /** The name of who it was sent to. */
  to: string;
  /** waiting until the other side starts on it, then its agent status. */
  status: string;
  sent_at: number | null;
}

export interface ActivityRoster {
  /** Keyed by Job id, which is the helper's execution id. */
  agents: Record<string, RosterFacts>;
  outgoing: OutgoingSend[];
}

export function getActivityRoster(sessionId: string, signal?: AbortSignal): Promise<ActivityRoster> {
  return jsonFetch<ActivityRoster>(`/api/session/${encodeURIComponent(sessionId)}/activity/roster`, { signal, cache: "no-store" });
}
