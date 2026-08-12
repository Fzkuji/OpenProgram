export interface TaskResourceEntry {
  targetHead?: string | null;
  finalHead?: string | null;
  status: string;
  resource?: Record<string, unknown> | null;
  updatedAt: number;
}

const TERMINAL = new Set(["completed", "cancelled", "errored"]);

export function selectResourceForHead(
  taskMap: Record<string, TaskResourceEntry>,
  headId: string,
  pendingPrefix: string,
): Record<string, unknown> | null {
  const matches = Object.entries(taskMap).filter(([taskId, entry]) => {
    const mapped = entry.finalHead || entry.targetHead || `${pendingPrefix}${taskId}`;
    return mapped === headId;
  });
  matches.sort((a, b) => {
    const aTerminal = TERMINAL.has(a[1].status) ? 1 : 0;
    const bTerminal = TERMINAL.has(b[1].status) ? 1 : 0;
    return aTerminal - bTerminal || b[1].updatedAt - a[1].updatedAt;
  });
  return matches[0]?.[1].resource || null;
}
