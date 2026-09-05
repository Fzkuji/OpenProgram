/** Apply only server snapshots at least as recent as the confirmed state. */
export function permissionSnapshotPatch(
  current: { permission_version?: number },
  snapshot: { mode?: unknown; version?: unknown },
): { permission_mode: string; effective_permission: string; permission_version: number } | null {
  const { mode, version } = snapshot;
  if (typeof mode !== "string" || !["ask", "acceptEdits", "plan", "auto", "bypass"].includes(mode)
    || typeof version !== "number" || !Number.isSafeInteger(version) || version < 0
    || version < (current.permission_version ?? 0)) return null;
  return { permission_mode: mode, effective_permission: mode, permission_version: version };
}
