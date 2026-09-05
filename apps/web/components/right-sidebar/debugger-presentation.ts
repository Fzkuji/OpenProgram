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
export function executionTitle(snapshot: ExecutionSnapshot, ordinal: number, text: Text): string {
  const display = snapshot.display;
  const name = display?.tool_name || display?.label || display?.entrypoint;
  if (name === "agent") return text("Assistant run", "助手执行");
  if (name === "goal") return text("Goal run", "目标执行");
  return name || text(`Run ${ordinal}`, `第 ${ordinal} 次执行`);
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
      title = effect.kind === "provider.before" ? text("Model request sent", "已发送模型请求") : text("External action started", "已开始外部操作");
    } else if (event.kind === "command.rejected") {
      title = text("Control request rejected", "控制请求被拒绝");
    }
    if (!title || rows.at(-1)?.title === title) continue;
    const time = state.updated_at ?? effect.dispatched_at ?? effect.updated_at;
    rows.push({ sequence: event.sequence, title, time: typeof time === "number" ? time : undefined });
  }
  return rows.reverse();
}
