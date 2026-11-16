export type SandboxEscalation = { from: string; to: string; path?: string; rule?: string; justification?: string };

export function readSandboxEscalation(args?: Record<string, unknown>): SandboxEscalation | undefined {
  const raw = args?._sandbox_escalation;
  if (!raw || typeof raw !== "object") return undefined;
  const o = raw as Record<string, unknown>;
  return {
    from: typeof o.from === "string" ? o.from : "",
    to: typeof o.to === "string" ? o.to : "",
    path: typeof o.path === "string" ? o.path : undefined,
    rule: typeof o.rule === "string" ? o.rule : undefined,
    justification: typeof o.justification === "string" ? o.justification : undefined,
  };
}

/** Tools whose approval shows a command line. */
export const COMMAND_TOOLS = ["bash", "shell", "exec_command"];

/** Text shared by the approval card and its editable discussion draft. */
export function approvalDisplayText(
  prompt: string, detail: string | undefined, escalation: SandboxEscalation | undefined,
  text: (en: string, zh: string) => string,
  tool = "",
): { prompt: string; summary: string } {
  if (!escalation) {
    // The Worker words a tool approval one way, in Chinese
    // (permissions/approvals/approval.py); the card says it in the reader's language.
    const worded = tool && prompt === `允许执行 ${tool}？`
      ? text(COMMAND_TOOLS.includes(tool) ? "Run this command?" : `Run ${tool}?`, prompt)
      : prompt;
    return { prompt: worded, summary: detail ?? "" };
  }
  return {
    prompt: text("Run this command outside the sandbox?", "允许这条命令在沙箱外执行？"),
    summary: [
      escalation.justification ?? "",
      escalation.path ? `${text("Blocked path", "被拦路径")}: ${escalation.path}` : "",
      escalation.rule ? `${text("Matched rule", "命中规则")}: ${escalation.rule}` : "",
    ].filter(Boolean).join("\n"),
  };
}
