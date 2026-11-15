/**
 * What an agent's card says on its one row: the state its badge names and
 * the few words of what it was asked. Pure functions of the card's facts,
 * so a reload, a live update and a test read the same thing.
 */
import type { ChatMsg } from "../../session-store/types";
import { isAgentFinished, isAgentRunning } from "./row-status.ts";

type Attach = NonNullable<ChatMsg["attach"]>;

export type AgentCardState =
  | "needs_approval"
  | "needs_input"
  | "queued"
  | "running"
  | "cancelling"
  | "failed"
  | "stopped"
  | "done"
  /** A reference the person attached by hand: no agent ran for it. */
  | "attached"
  /** An older reference with no state to name. */
  | "none";

/** The state the row's badge names. A question the agent waits on comes
 *  first: it is what the person has to act on. */
export function agentCardState(attach: Attach, waiting: "approval" | "input" | null): AgentCardState {
  const status = attach.status;
  if (!status) return attach.manual ? "attached" : "none";
  if (status === "cancelling") return "cancelling";
  if (isAgentRunning(status)) {
    if (waiting === "approval") return "needs_approval";
    if (waiting === "input") return "needs_input";
    return status === "running" ? "running" : "queued";
  }
  if (!isAgentFinished(status)) return "none";
  if (attach.result_status === "failed" || status === "errored" || (status as string) === "failed") return "failed";
  if (attach.result_status === "stopped" || status === "cancelled") return "stopped";
  return "done";
}

/** The agent has not ended: its time still runs and it can be cancelled. */
export function agentCardLive(state: AgentCardState): boolean {
  return state === "running" || state === "queued" || state === "cancelling"
    || state === "needs_approval" || state === "needs_input";
}

const TASK_CHARS = 120;
const RECEIPT_LINE = /^\[(?:task|message) from [^\]\n]*\][^\n]*\n+/;

/** What the agent was asked, as the row shows it: the first line of its
 *  prompt without the delivery receipt. The row cuts it to its width. */
export function taskWords(prompt: string | undefined): string {
  const body = (prompt || "").replace(RECEIPT_LINE, "");
  const line = body.split("\n").map((l) => l.trim()).find(Boolean) || "";
  const flat = line.replace(/\s+/g, " ");
  return flat.length > TASK_CHARS ? `${flat.slice(0, TASK_CHARS).trimEnd()}…` : flat;
}

/** A USD amount the server sent as text; null when it sent none. */
export function costOf(value: string | null | undefined): number | null {
  if (value == null || value === "") return null;
  const cost = Number(value);
  return Number.isFinite(cost) && cost >= 0 ? cost : null;
}
