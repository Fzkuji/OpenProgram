/** Recents-list fields. Other ConvSummary writes (workspace_alignment, …)
 *  must not rebuild the sidebar table. */
const RECENTS_FIELDS = [
  "title",
  "preview",
  "pinned",
  "archived",
  "group",
  "updated_at",
  "created_at",
  "project",
  "unread",
  "status",
  "channel",
] as const;

export type RecentsConv = {
  [K in (typeof RECENTS_FIELDS)[number]]?: unknown;
};

export function recentsConversationsEqual(
  a: Record<string, RecentsConv>,
  b: Record<string, RecentsConv>,
): boolean {
  if (a === b) return true;
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const id of aKeys) {
    const x = a[id];
    const y = b[id];
    if (!y) return false;
    for (const f of RECENTS_FIELDS) {
      if (x[f] !== y[f]) return false;
    }
  }
  return true;
}

/** Sidebar only reads `!!runningTasks[id]`. */
export function runningIdSetEqual(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  if (a === b) return true;
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const id of aKeys) {
    if (!(id in b)) return false;
  }
  return true;
}
