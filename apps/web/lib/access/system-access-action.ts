/** Choose only the next action for an explicitly active, visible recovery. */
export function systemAccessAction(local: boolean, visible: boolean, armed: boolean,
  checked: boolean, pending: boolean, missing: string[], requested: Set<string>) {
  if (!local || !visible || !armed || !checked || pending) return null;
  if (!missing.length) return null;
  if (!requested.has(missing[0])) return { type: "request" as const, id: missing[0] };
  return null;
}
