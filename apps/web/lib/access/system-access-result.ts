export function systemAccessRequired(output: unknown): string[] {
  let value = output;
  if (typeof value === "string") { try { value = JSON.parse(value); } catch { return []; } }
  if (!value || typeof value !== "object") return [];
  const result = value as Record<string, unknown>;
  if (result.reason_code !== "system_access_required" || result.status !== "infeasible" || !Array.isArray(result.system_access)) return [];
  return [...new Set(result.system_access.filter(row => row && row.status === "not_granted" && ["screen_recording", "accessibility"].includes(row.id)).map(row => String(row.id)))];
}

const liveRequests = new Map<string, boolean>();
export function rememberSystemAccessRequest(session: string, id: string, output: unknown): void {
  if (!systemAccessRequired(output).length) return;
  const key = `${session}:${id}`;
  if (liveRequests.has(key)) return;
  liveRequests.set(key, true);
  if (liveRequests.size > 128) liveRequests.delete(liveRequests.keys().next().value!);
}

/** Marker key for a live GUI grant. Prefer the code-node path; the
 *  runtime row may still be keyed by the envelope msg_id. */
export function systemAccessRequestId(tree: unknown, fallbackId: string): string {
  if (tree && typeof tree === "object" && !Array.isArray(tree)) {
    const path = (tree as { path?: unknown }).path;
    if (typeof path === "string" && path) return path;
  }
  return fallbackId;
}

/** Live completion id is the persisted code-node path. Envelope msg_id is
 *  the assistant caller for LLM-issued runs and must not be the marker key. */
export function rememberSystemAccessFromTree(
  session: string,
  envelopeId: string | undefined,
  tree: unknown,
  functionName?: string,
): void {
  const node = tree && typeof tree === "object" && !Array.isArray(tree)
    ? tree as { path?: unknown; name?: unknown; output?: unknown }
    : undefined;
  const fn = functionName || (typeof node?.name === "string" ? node.name : "");
  if (fn !== "gui_agent") return;
  const id = systemAccessRequestId(node, envelopeId || "");
  if (!id) return;
  rememberSystemAccessRequest(session, id, node?.output);
}
export function hasSystemAccessRequest(session: string, id: string): boolean {
  return liveRequests.get(`${session}:${id}`) === true;
}
export function consumeSystemAccessRequest(session: string, id: string): void {
  liveRequests.set(`${session}:${id}`, false);
}
