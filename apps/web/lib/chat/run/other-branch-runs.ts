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
 *
 * Frames and lists carry the server's `run_seq`, taken when each was made.
 * A list is read after its seq is taken, and its answer can arrive after a
 * frame made later (a run that ended meanwhile): a branch keeps what the
 * newer of the two says, and a frame older than the last list is dropped.
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

/** The conversations with a turn in progress on a branch a pane may not
 *  show, keyed by id. The sidebar row of such a conversation shows it
 *  running whichever branch is selected: a Goal or a message sent to
 *  another branch works there while the person looks elsewhere. A run
 *  that waits on the person or has failed is not in progress. */
export function sessionsWithBranchRun(runs: Runs): Record<string, true> {
  const found: Record<string, true> = {};
  for (const [sid, branches] of Object.entries(runs)) {
    if (Object.values(branches).some((run) => run.state === "running")) found[sid] = true;
  }
  return found;
}

/** The theme tone a run's badge takes: waiting on the person is a warning,
 *  a failure a danger, a run in progress neutral. */
export function badgeTone(state: OtherBranchState): "neutral" | "warning" | "danger" {
  return state === "failed" ? "danger" : state === "running" ? "neutral" : "warning";
}

/** The seqs applied so far for each conversation: its last list's, and
 *  each branch's last frame's. */
interface Applied {
  listed?: number;
  frames: Record<string, number>;
}

const seqOf = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

interface OtherBranchRunsStore {
  runs: Runs;
  applied: Record<string, Applied>;
  applyFrame(type: string, data: Record<string, unknown>): void;
  /** Replace a conversation's runs with the ones a load lists, as of
   *  `runSeq` when the list carries one. */
  seed(sid: string, running: unknown, runSeq?: unknown): void;
}

export const useOtherBranchRuns = create<OtherBranchRunsStore>((set) => ({
  runs: {},
  applied: {},
  applyFrame: (type, data) => set((state) => {
    const sid = data.session_id, branchId = data.branch_id, seq = seqOf(data.run_seq);
    if (seq === undefined || typeof sid !== "string" || typeof branchId !== "string") {
      return { runs: applyBranchFrame(state.runs, type, data) };
    }
    const applied = state.applied[sid] ?? { frames: {} };
    if (seq < (applied.listed ?? -Infinity) || seq < (applied.frames[branchId] ?? -Infinity)) return state;
    return {
      runs: applyBranchFrame(state.runs, type, data),
      applied: { ...state.applied, [sid]: { ...applied, frames: { ...applied.frames, [branchId]: seq } } },
    };
  }),
  seed: (sid, running, runSeq) => set((state) => {
    const seq = seqOf(runSeq);
    const applied = state.applied[sid] ?? { frames: {} };
    if (seq !== undefined && seq < (applied.listed ?? -Infinity)) return state;
    const entries: Record<string, OtherBranchRun> = {};
    for (const item of Array.isArray(running) ? running : []) {
      if (!item || typeof item !== "object" || (item as { selected?: unknown }).selected) continue;
      const run = runFromFrame(item as Record<string, unknown>);
      if (run) entries[run.branchId] = run;
    }
    if (seq === undefined) return { runs: { ...state.runs, [sid]: entries } };
    // A branch whose frame is newer than this list keeps what the frame said.
    const current = state.runs[sid] ?? {};
    for (const [branchId, frameSeq] of Object.entries(applied.frames)) {
      if (frameSeq <= seq) continue;
      if (current[branchId]) entries[branchId] = current[branchId];
      else delete entries[branchId];
    }
    return {
      runs: { ...state.runs, [sid]: entries },
      applied: { ...state.applied, [sid]: { ...applied, listed: seq } },
    };
  }),
}));
