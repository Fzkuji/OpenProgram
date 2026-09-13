import type { ConversationActivityBranch, ExecutionSnapshot } from "./execution-debugger";

export type ActivityBranch = ConversationActivityBranch & { executions: ExecutionSnapshot[] };

/** The server supplies real DAG tips; execution order cannot identify a fork. */
export function activityBranches(executions: ExecutionSnapshot[], branches: ConversationActivityBranch[]) {
  const associated = new Set(branches.flatMap(branch => branch.execution_ids));
  const groups: ActivityBranch[] = branches.map(branch => {
    const members = new Set(branch.execution_ids);
    // Programs without their own conversation branch stay with their owner.
    let changed = true;
    while (changed) {
      changed = false;
      for (const item of executions) {
        const parent = item.view_parent_execution_id ?? item.parent_execution_id;
        if (!associated.has(item.execution_id) && !members.has(item.execution_id) && parent && members.has(parent)) {
          members.add(item.execution_id);
          changed = true;
        }
      }
    }
    return { ...branch, executions: executions.filter(item => members.has(item.execution_id)) };
  }).filter(branch => branch.executions.length > 0);
  const grouped = new Set(groups.flatMap(branch => branch.executions.map(item => item.execution_id)));
  return { groups, ungrouped: executions.filter(item => !grouped.has(item.execution_id)) };
}
