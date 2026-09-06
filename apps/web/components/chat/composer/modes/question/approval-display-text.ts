export type SandboxEscalation = { from: string; to: string; path?: string; rule?: string };

export function readSandboxEscalation(args?: Record<string, unknown>): SandboxEscalation | undefined {
  const raw = args?._sandbox_escalation;
  if (!raw || typeof raw !== "object") return undefined;
  const o = raw as Record<string, unknown>;
  return {
    from: typeof o.from === "string" ? o.from : "",
    to: typeof o.to === "string" ? o.to : "",
    path: typeof o.path === "string" ? o.path : undefined,
    rule: typeof o.rule === "string" ? o.rule : undefined,
  };
}

/** Text shared by the approval card and its editable discussion draft. */
export function approvalDisplayText(
  prompt: string, detail: string | undefined, escalation: SandboxEscalation | undefined,
  text: (en: string, zh: string) => string,
): { prompt: string; summary: string } {
  if (!escalation) return { prompt, summary: detail ?? "" };
  return {
    prompt: text("Sandbox blocked this access. Approve an escalated retry?", "沙箱拦截了这次访问。是否批准升级后重试？"),
    summary: [
      escalation.path ? `${text("Blocked path", "被拦路径")}: ${escalation.path}` : "",
      escalation.rule ? `${text("Matched rule", "命中规则")}: ${escalation.rule}` : "",
    ].filter(Boolean).join("\n"),
  };
}
