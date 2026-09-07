export function systemAccessRequired(output: unknown): string[] {
  let value = output;
  if (typeof value === "string") { try { value = JSON.parse(value); } catch { return []; } }
  if (!value || typeof value !== "object") return [];
  const result = value as Record<string, unknown>;
  if (result.reason_code !== "system_access_required" || result.status !== "infeasible" || !Array.isArray(result.system_access)) return [];
  return [...new Set(result.system_access.filter(row => row && row.status === "not_granted" && ["screen_recording", "accessibility"].includes(row.id)).map(row => String(row.id)))];
}
