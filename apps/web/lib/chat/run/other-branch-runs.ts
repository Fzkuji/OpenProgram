/**
 * What runs on the branches of a conversation other than the one a pane
 * shows, so the pane can say so beside its composer and switch to it.
 *
 * The server announces every branch's run with `branch_running_task`
 * (its name and `wait_state`: `approval`, `input` or null while it runs)
 * and its end with `branch_running_task_clear`. `session_loaded` lists the
 * runs already going (`running_branches`). An entry leaves when its run
 * ends; the pane never stops another branch from here (Stop stays in that
 * branch's own view).
 */
import { create } from "zustand";

export type OtherBranchState = "running" | "approval" | "input" | "failed";

export interface OtherBranchRun {
  branchId: string;
  name: string | null;
  state: OtherBranchState;
  msgId?: string;
  executionId?: string;
}

type Runs = Record<string, Record<string, OtherBranchRun>>;

/** The run a `branch_running_task` frame describes. */
export function runFromFrame(data: Record<string, unknown>): OtherBranchRun | null {
  const branchId = data.branch_id;
  if (typeof branchId !== "string" || !branchId) return null;
  const wait = data.wait_state;
  const state: OtherBranchState = data.status === "failed" ? "failed"
    : wait === "approval" ? "approval" : wait === "input" ? "input" : "running";
  return {
    branchId,
    name: typeof data.branch_name === "string" && data.branch_name ? data.branch_name : null,
    state,
    ...(typeof data.msg_id === "string" ? { msgId: data.msg_id } : {}),
    ...(typeof data.execution_id === "string" ? { executionId: data.execution_id } : {}),
  };
}

/** `runs` after one frame: a run frame sets its branch's entry, a clear
 *  frame removes it when it names that run (or names no run at all). */
export function applyBranchFrame(runs: Runs, type: string, data: Record<string, unknown>): Runs {
  const sid = data.session_id;
  const branchId = data.branch_id;
  if (typeof sid !== "string" || !sid || typeof branchId !== "string" || !branchId) return runs;
  const current = runs[sid] ?? {};
  if (type === "branch_running_task") {
    const run = runFromFrame(data);
    return run ? { ...runs, [sid]: { ...current, [branchId]: run } } : runs;
  }
  if (type !== "branch_running_task_clear" || !current[branchId]) return runs;
  const held = current[branchId];
  const names = (data.execution_id ?? data.msg_id) !== undefined;
  const matches = (data.execution_id !== undefined && data.execution_id === held.executionId)
    || (data.msg_id !== undefined && data.msg_id === held.msgId);
  if (names && !matches) return runs;
  const { [branchId]: _gone, ...rest } = current;
  return { ...runs, [sid]: rest };
}

/** The runs a pane showing `selected` lists: every other branch's. */
export function runsBeside(runs: Runs, sid: string, selected: string | null | undefined): OtherBranchRun[] {
  return Object.values(runs[sid] ?? {}).filter((run) => run.branchId !== selected);
}

/** The theme tone a run's badge takes: waiting on the person is a warning,
 *  a failure a danger, a run in progress neutral. */
export function badgeTone(state: OtherBranchState): "neutral" | "warning" | "danger" {
  return state === "failed" ? "danger" : state === "running" ? "neutral" : "warning";
}

interface OtherBranchRunsStore {
  runs: Runs;
  applyFrame(type: string, data: Record<string, unknown>): void;
  /** Replace a conversation's runs with the ones a load lists. */
  seed(sid: string, running: unknown): void;
}

export const useOtherBranchRuns = create<OtherBranchRunsStore>((set) => ({
  runs: {},
  applyFrame: (type, data) => set((state) => ({ runs: applyBranchFrame(state.runs, type, data) })),
  seed: (sid, running) => set((state) => {
    const entries: Record<string, OtherBranchRun> = {};
    for (const item of Array.isArray(running) ? running : []) {
      if (!item || typeof item !== "object" || (item as { selected?: unknown }).selected) continue;
      const run = runFromFrame(item as Record<string, unknown>);
      if (run) entries[run.branchId] = run;
    }
    return { runs: { ...state.runs, [sid]: entries } };
  }),
}));
