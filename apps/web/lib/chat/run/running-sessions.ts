/**
 * Run state for the conversations a client is not looking at.
 *
 * `running_task` and `branch_running_task` announce a turn as it starts;
 * a client that connects while turns are already going hears nothing for
 * them. It asks once per connection (`get_running_sessions`) and the
 * server answers `running_sessions`: one run state per conversation with
 * a turn running, on any branch, each shaped like a `run_state` frame.
 * A conversation this client still shows as running and the answer does
 * not list ended while the client was away: it is asked about by itself
 * (`get_run_state`), whose answer clears it.
 */
import { acceptRunFrame } from "@/lib/chat/decisions/run-seq";
import { useOtherBranchRuns } from "./other-branch-runs";

export interface RunStateData {
  session_id?: unknown;
  run_active?: unknown;
  run_task?: { msg_id?: string; execution_id?: string; status_version?: number; op_kind?: string; op_id?: string } | null;
  running_branches?: unknown;
  run_seq?: unknown;
}

/** The run states a `running_sessions` frame lists. */
export function listedRunStates(data: unknown): RunStateData[] {
  const sessions = (data as { sessions?: unknown } | null | undefined)?.sessions;
  if (!Array.isArray(sessions)) return [];
  return sessions.filter((item): item is RunStateData =>
    !!item && typeof item === "object" && typeof (item as RunStateData).session_id === "string"
    && !!(item as RunStateData).session_id);
}

/** The conversations shown as running here that the server did not list.
 *  `loading` is the conversation whose load is already on its way and
 *  carries its own run state. */
export function unlistedRunning(
  listed: readonly RunStateData[],
  shownRunning: Iterable<string>,
  loading?: string | null,
): string[] {
  const named = new Set(listed.map((state) => state.session_id as string));
  return [...new Set(shownRunning)].filter((sid) => sid && sid !== loading && !named.has(sid)).sort();
}

/** Apply one run state (a `run_state` frame, or an entry of
 *  `running_sessions`): the selected branch's turn takes or frees the
 *  conversation's running slot, and the turns on its other branches
 *  replace the ones held for it. A state older than a run frame already
 *  applied for the conversation is dropped. */
export function applyRunState(data: RunStateData | null | undefined): void {
  const sid = data?.session_id;
  if (typeof sid !== "string" || !sid || !acceptRunFrame(sid, data?.run_seq)) return;
  if (Array.isArray(data?.running_branches)) {
    useOtherBranchRuns.getState().seed(sid, data.running_branches, data.run_seq);
  }
  const active = data?.run_active === true;
  const task = data?.run_task;
  void import("@/lib/chat/send/send-queue").then((m) => m.reconcileAfterSessionLoad(sid, active, task));
}
