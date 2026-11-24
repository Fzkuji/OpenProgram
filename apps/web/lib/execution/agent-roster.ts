/**
 * A helper in Activity: which status it wears, whether it is still going,
 * and what its work cost.
 *
 * The page knows at once what runs and what waits on the reader (the
 * executions and the chat's open decisions); the server adds what only it
 * can tell (stalled, queued, stopped by a limit) and what the work cost.
 */
import type { RosterFacts } from "@/lib/net/execution/activity-roster-client";
import { costOf } from "../chat/transcript/agent-card.ts";

export type RosterStatus = "running" | "approval" | "input" | "stalled" | "failed" | "idle" | "waiting";

/** What the page sees of a helper right now: it waits on an approval or
 *  an answer, it runs, it ended in an error, or it is at rest. */
export type RosterLook = "approval" | "input" | "run" | "err" | "rest";

export function rosterStatus(look: RosterLook, facts?: RosterFacts | null): RosterStatus {
  if (look === "approval") return "approval";
  if (look === "input") return facts?.agent_status === "needs approval" ? "approval" : "input";
  if (look === "run") {
    if (facts?.agent_status === "needs approval") return "approval";
    if (facts?.agent_status === "needs input") return "input";
    if (facts?.agent_status === "stalled") return "stalled";
    return facts?.queued ? "waiting" : "running";
  }
  // A helper a limit stopped ended as cancelled, which the page reads as rest.
  return look === "err" || facts?.agent_status === "failed" ? "failed" : "idle";
}

/** Still going or waiting on the reader. */
export const rosterLive = (status: RosterStatus): boolean => status !== "idle" && status !== "failed";
/** Waits on the reader. */
export const rosterNeedsReader = (status: RosterStatus): boolean => status === "approval" || status === "input";

/** A helper's subtree cost in dollars, when every price in it is known. */
export function rosterCost(facts?: RosterFacts | null): number | null {
  return costOf(facts?.usage?.cost_usd);
}

/** The known costs added up; null when none is known. */
export function rosterTotal(costs: (number | null)[]): number | null {
  const known = costs.filter((cost): cost is number => cost !== null);
  return known.length ? known.reduce((sum, cost) => sum + cost, 0) : null;
}
