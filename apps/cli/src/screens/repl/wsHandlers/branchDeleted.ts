/** What a `branch_deleted` reply tells the person, and whether the
 *  transcript must be reloaded because the selected branch was deleted
 *  and the conversation moved to another one (`new_head`). */
export interface BranchDeletedData {
  session_id?: string;
  ok?: boolean;
  deleted?: number;
  new_head?: string | null;
  error?: string | null;
}

export function branchDeletedOutcome(d: BranchDeletedData): { text: string; reload: boolean } {
  if (!d.ok) return { text: `Branch not deleted: ${d.error || 'unknown error'}`, reload: false };
  const count = typeof d.deleted === 'number' ? ` (${d.deleted} messages removed)` : '';
  if (d.new_head) return { text: `Deleted the branch${count}; switched to another branch.`, reload: true };
  return { text: `Deleted the branch${count}.`, reload: false };
}
