/** Read-only controller projection shared by chat and Running. */
export type SelfUpdate = {
  update_id: string;
  session_id: string;
  origin_assistant_id: string;
  root_id: string;
  parent_id: string | null;
  phase: "preparing" | "staging" | "ready" | "activating" | "verifying" |
    "succeeded" | "aborted" | "rolled_back" | "needs_manual_recovery";
  attempt: number;
  state_revision: number;
  snapshot_id: string;
  created_at: number;
  updated_at: number;
  candidate_revision: string;
  changed_paths: string[];
  target_app: string;
  last_verified_runtime: {
    candidate_sha: string; worker_pid: number; verified_at: number; source: string;
  } | null;
  rollback_available: boolean;
  verifier_verdict: string | null;
  verifier: {
    verdict: string;
    assertions: { id: string; status: string; evidence_refs: string[] }[];
    evidence_id: string;
  } | null;
  diagnosis: { status: string; at: number } | null;
  source_repair_result: { status: string; at: number; candidate_sha?: string } | null;
  iteration: {
    root_id: string; parent_id: string | null; attempt: number; max_attempts: number;
    deadline: number; stopped: boolean;
    submission: { status: string; child_id: string | null; at: number } | null;
  } | null;
};

export type SelfUpdatePage = { items: SelfUpdate[]; next_cursor: string | null };

export function groupSelfUpdates(items: SelfUpdate[]): SelfUpdate[][] {
  const groups = new Map<string, SelfUpdate[]>();
  for (const item of [...items].sort((a, b) => b.created_at - a.created_at || a.update_id.localeCompare(b.update_id))) {
    const group = groups.get(item.root_id) ?? [];
    group.push(item);
    groups.set(item.root_id, group);
  }
  return [...groups.values()].map((group) => group.sort((a, b) => a.attempt - b.attempt));
}
