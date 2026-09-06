import type { ExecutionSnapshot } from "@/lib/execution-debugger";
import type { PersistedExecutionEvent } from "@/lib/net/execution-client";

type Text = (en: string, zh: string) => string;
const STATUS: Record<string, [string, string]> = {
  queued: ["Waiting to start", "等待开始"], running: ["Running", "正在执行"],
  pausing: ["Pausing", "正在暂停"], paused: ["Paused", "已暂停"],
  cancelling: ["Stopping", "正在停止"], reconciliation_required: ["Result needs confirmation", "结果待确认"],
  completed: ["Completed", "已完成"], failed: ["Failed", "执行失败"],
  cancelled: ["Stopped", "已停止"], interrupted: ["Interrupted", "执行中断"],
};
export function statusLabel(status: string, text: Text): string {
  return text(...(STATUS[status] || ["Status unavailable", "状态不可用"]));
}
export function executionNeedsAttention(snapshot: ExecutionSnapshot): boolean {
  return snapshot.status === "paused" || (snapshot.status === "reconciliation_required"
    && snapshot.effect_summary?.provider_response_incomplete !== true);
}
export function executionStatusLabel(snapshot: ExecutionSnapshot, text: Text): string {
  return snapshot.status === "reconciliation_required" && !executionNeedsAttention(snapshot)
    ? text("Ended · response record incomplete", "已结束 · 响应记录不完整")
    : statusLabel(snapshot.status, text);
}
export function executionRequest(snapshot: ExecutionSnapshot): string {
  return (snapshot.task_label || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
}
export function executionTitle(snapshot: ExecutionSnapshot, ordinal: number, text: Text): string {
  const display = snapshot.display;
  const request = executionRequest(snapshot);
  const label = display?.kind === "job_agent" && display.label ? display.label : request;
  if (label) {
    const characters = Array.from(label);
    const concise = characters.length > 72 ? characters.slice(0, 71).join("") + "…" : label;
    return snapshot.parent_execution_id && display?.kind !== "job_agent" ? `${text("Branch", "分支")}: ${concise}` : concise;
  }
  const name = display?.tool_name || display?.label || display?.entrypoint;
  if (name === "agent" || (display?.kind === "chat" && name === "main") || name === "openprogram.agent.production_driver:AgentProductionDriver") return text(`Assistant task ${ordinal}`, `助手任务 ${ordinal}`);
  if (name === "goal") return text("Goal", "目标任务");
  return name || text(`Task ${ordinal}`, `任务 ${ordinal}`);
}
export function executionGuidance(snapshot: ExecutionSnapshot, text: Text): string | null {
  if (snapshot.status === "reconciliation_required" && !executionNeedsAttention(snapshot)) return text("This attempt has ended. Its model response record is incomplete; there is no pending confirmation. Recorded details remain available in history.", "本轮执行已结束，模型响应记录不完整，没有待确认事项。详细记录保留在历史中。");
  if (snapshot.status === "reconciliation_required") return text("An external action has no confirmed result. Inspect its recorded details before repeating it.", "外部操作尚无已确认的结果。再次执行前请核对记录详情。");
  if (snapshot.status === "paused") return snapshot.can_continue
    ? text("Paused at a saved point. Continue resumes this task; Step advances one supported boundary.", "任务已暂停在保存点。继续会恢复任务；单步只推进一个支持的执行边界。")
    : text("This task is paused, but continuation is currently unavailable. Technical details contains the saved state and recorded reasons.", "任务已暂停，但当前无法继续。技术详情中保留了保存状态和原因记录。");
  if (snapshot.status === "failed") return snapshot.reason_code === "agent_runner_error"
    ? text("The Agent runtime failed. Review the recorded error before retrying from a saved point.", "Agent 运行时发生错误。请先查看错误记录，再从保存点重试。")
    : text("This task failed. Review its recorded progress and technical details before retrying.", "任务执行失败。重试前请查看进展和技术详情中的记录。");
  if (snapshot.status === "interrupted") return text("Execution stopped before a final result was saved. Its history remains available.", "执行在保存最终结果前中断，历史记录仍然保留。");
  return null;
}
export function shortTime(value: number): string {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
export function updatedTime(value: number): string {
  return new Date(value * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}
export function activityRows(events: PersistedExecutionEvent[], text: Text) {
  const rows: Array<{ sequence: number; title: string; time?: number }> = [];
  for (const event of events) {
    const payload = event.payload || {};
    const state = record(payload.record);
    const effect = record(payload.effect);
    let title = "";
    if (event.kind.startsWith("execution.") && typeof state.status === "string") {
      title = statusLabel(state.status, text);
    } else if (event.kind === "effect.dispatched") {
      title = record(effect.metadata).kind === "provider.before" ? text("Model request sent", "已发送模型请求") : text("External action started", "已开始外部操作");
    } else if (event.kind === "command.rejected") {
      title = text("Control request rejected", "控制请求被拒绝");
    }
    if (!title || rows.at(-1)?.title === title) continue;
    const time = state.updated_at ?? effect.dispatched_at ?? effect.updated_at;
    rows.push({ sequence: event.sequence, title, time: typeof time === "number" ? time : undefined });
  }
  return rows.reverse();
}
