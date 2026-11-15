/**
 * Extract the capability ids from a structured system-access result.
 *
 * Capability ids are owned by the backend registry. The client deliberately
 * does not maintain an allow-list: new registry entries must remain visible in
 * recovery UI without a frontend release. Legacy screen_recording and
 * accessibility payloads therefore continue to work unchanged.
 */
export function systemAccessRequired(output: unknown): string[] {
  let value = output;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return []; }
  }
  if (!value || typeof value !== "object") return [];
  const result = value as Record<string, unknown>;
  if (result.reason_code !== "system_access_required" || result.status !== "infeasible" || !Array.isArray(result.system_access)) return [];
  return [...new Set(result.system_access
    .filter(row => row && typeof row === "object" && typeof (row as { id?: unknown }).id === "string")
    .filter(row => {
      const status = String((row as { status?: unknown }).status || "not_granted");
      return status !== "granted";
    })
    .map(row => String((row as { id: string }).id)))];
}
