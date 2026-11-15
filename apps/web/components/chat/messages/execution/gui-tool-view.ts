import type { TNode } from "./tree-types";

type RecordValue = Record<string, unknown>;
type Text = (en: string, zh: string) => string;
export const GUI_TOOL_NAMES = new Set(["gui_agent", "computer_use", "browser_use", "vm_use"]);

export interface GuiStepView {
  capability: string;
  target: string;
  status: string;
  /** Why the step failed or stopped; empty for steps that went through. */
  reason: string;
}

export interface GuiToolView {
  status: string;
  summary: string;
  blocker: string;
  handoff: string;
  steps: GuiStepView[];
}

function object(value: unknown): RecordValue {
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return {}; }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}

function string(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** The public `computer_use` tool shares its name with the GUI Agent's internal
 *  desktop capability. Only the capability (task / app_name, no `action`) is a
 *  GUI step; the public tool keeps the ordinary tool renderer. */
export function isGuiToolCall(name: string, params?: unknown): boolean {
  if (!GUI_TOOL_NAMES.has(name)) return false;
  return name !== "computer_use" || typeof object(params).action !== "string";
}

export function guiStatus(output: unknown, executionStatus = ""): string {
  const result = object(output);
  // A stop on this execution takes precedence over its last successful step.
  if (["cancelled", "canceled", "cancelling", "interrupted", "paused", "pausing"].includes(executionStatus)) {
    return executionStatus;
  }
  if (["running", "streaming", "pending", "queued"].includes(executionStatus)) return "running";
  const status = string(result.status);
  if (status === "infeasible" || result.infeasible_declared === true) return "infeasible";
  if (result.success === false || result.ok === false) return "failed";
  if (["applied", "succeeded", "failed", "cancelled", "interrupted", "paused"].includes(status)) return status;
  if (executionStatus === "error") return "failed";
  // Function transport completion alone is not task-success evidence.
  return "unknown";
}

export function guiStatusLabel(status: string, text: Text): string {
  const labels: Record<string, [string, string]> = {
    running: ["Running", "运行中"], applied: ["Action applied", "动作已执行"],
    succeeded: ["Succeeded", "成功"], failed: ["Failed", "失败"],
    infeasible: ["Needs takeover", "需要接手"], cancelled: ["Cancelled", "已取消"],
    canceled: ["Cancelled", "已取消"], cancelling: ["Stopping…", "正在停止…"],
    interrupted: ["Interrupted", "已中断"], paused: ["Paused", "已暂停"],
    pausing: ["Pausing…", "正在暂停…"], unknown: ["Result unavailable", "结果不可用"],
  };
  return text(...(labels[status] ?? labels.unknown));
}

export function guiCapabilityLabel(name: string, text: Text): string {
  return name === "computer_use" ? text("Computer Use", "电脑操作")
    : name === "browser_use" ? text("Browser Use", "浏览器操作")
    : name === "vm_use" ? text("VM Use", "虚拟机操作") : "GUI Agent";
}

function stepView(capability: string, input: unknown, output: unknown, executionStatus = ""): GuiStepView {
  const params = object(input), result = object(output), step = object(result.step);
  const actionResult = object(step.exec_result);
  const status = guiStatus(actionResult.success === false ? { ...result, success: false } : result, executionStatus);
  const stopped = status === "failed" || status === "infeasible";
  return {
    capability,
    target: string(object(result.target).app_name) || string(params.app_name) || string(params.url),
    status,
    reason: stopped
      ? string(result.blocker) || string(step.blocker) || string(actionResult.error) || string(result.summary)
      : "",
  };
}

/** Project only GUI-owned data; generic tool results retain their existing renderer. */
export function guiToolView(node: {
  name: string; status: string; output?: unknown; params?: RecordValue; tree_root?: unknown;
}): GuiToolView | null {
  if (!isGuiToolCall(node.name, node.params)) return null;
  const tree = object(node.tree_root) as TNode;
  const output = object(node.output ?? tree.output);
  const root = stepView(node.name, node.params, output, node.status);
  const blocker = string(output.blocker);
  const handoff = string(output.handoff_instruction);
  const rawSummary = string(output.summary);
  // An infeasible result usually repeats its handoff as the summary.
  // A direct capability call may explain its failure only in the action result.
  const summary = rawSummary && (rawSummary === handoff || rawSummary === blocker) ? ""
    : rawSummary || (blocker || handoff ? "" : root.reason);
  const steps: GuiStepView[] = [];
  if (Array.isArray(output.history)) {
    for (const value of output.history) {
      const entry = object(value);
      if (entry.type === "capability_call" && GUI_TOOL_NAMES.has(string(entry.capability))) {
        steps.push(stepView(string(entry.capability), entry.input, entry.output));
      }
    }
  }
  if (!steps.length && node.name === "gui_agent") {
    const seen = new Set<TNode>();
    const visit = (item: TNode) => {
      if (seen.has(item) || item.expose === "hidden") return;
      seen.add(item);
      if (item.name && GUI_TOOL_NAMES.has(item.name) && item.name !== "gui_agent") {
        steps.push(stepView(item.name, item.params, item.output, item.status));
        return; // A capability owns its nested planner and action calls.
      }
      for (const child of item.children ?? []) visit(child);
    };
    for (const child of tree.children ?? []) visit(child);
  }
  return { status: root.status, summary, blocker, handoff, steps };
}

/** One-line timeline note that agrees with the Details status. */
export function guiResultNote(
  name: string, output: unknown, executionStatus: string, text: Text, params?: RecordValue,
): string {
  const view = guiToolView({ name, status: executionStatus, output, params });
  if (!view) return "";
  const detail = view.handoff || view.blocker || view.summary;
  return [guiStatusLabel(view.status, text), detail].filter(Boolean).join(" · ");
}
