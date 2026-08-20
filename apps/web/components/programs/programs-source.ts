export const WORKFLOW_CAPABILITY_NAMES = [
  "search_workflows",
  "create_workflow",
  "revise_workflow",
  "auto_workflow",
] as const;

export type WorkflowCapabilityName = (typeof WORKFLOW_CAPABILITY_NAMES)[number];

const CAPABILITY_NAMES = new Set<string>(WORKFLOW_CAPABILITY_NAMES);

export type ProgramSourceEntry = {
  name?: string | null;
  path?: string | null;
  callable_name?: string | null;
  program_kind?: string | null;
};

export function workflowCapabilityName(
  entry: ProgramSourceEntry | null | undefined,
): WorkflowCapabilityName | null {
  if (!entry) return null;
  const tokens = [
    entry.callable_name,
    entry.name,
    ...(entry.path ?? "").split(/[\\/]/).map((part) => part.replace(/\.py$/, "")),
  ];
  for (const token of tokens) {
    if (token && CAPABILITY_NAMES.has(token)) return token as WorkflowCapabilityName;
  }
  return null;
}

export function isWorkflowCapability(entry: ProgramSourceEntry | null | undefined) {
  return workflowCapabilityName(entry) !== null;
}

export function isUserManualWorkflowEntry(entry: ProgramSourceEntry | null | undefined) {
  return workflowCapabilityName(entry) === "auto_workflow";
}

export function programSourceCategory(entry: ProgramSourceEntry | null | undefined) {
  if (isUserManualWorkflowEntry(entry)) return "auto_entry";
  if (isWorkflowCapability(entry)) return "workflow_capability";
  if (entry?.program_kind === "workflow") return "workflow";
  return entry?.program_kind ?? null;
}
