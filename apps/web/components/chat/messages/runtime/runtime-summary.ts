import { isLive } from "../../../../lib/chat/transcript/row-status.ts";
type RuntimeTreeSummary = {
  status?: string;
  output?: unknown;
  duration_ms?: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  children?: RuntimeTreeSummary[];
};

type RuntimeSummaryInput = {
  fnName: string;
  status?: string;
  timestamp?: number;
  now?: number;
  tree?: RuntimeTreeSummary | null;
  text?: (en: string, zh: string) => string;
};

const GUI_OUTCOMES = new Set(["succeeded", "failed", "infeasible", "cancelled"]);

function epochMs(value: number): number {
  return value > 1e12 ? value : value * 1000;
}

function durationMs(input: RuntimeSummaryInput): number | null {
  const { timestamp, now = Date.now(), tree } = input;
  if (Number.isFinite(tree?.duration_ms)) return Math.max(0, tree!.duration_ms!);
  if (tree?.start_time && tree.end_time) {
    return Math.max(0, epochMs(tree.end_time) - epochMs(tree.start_time));
  }
  if (timestamp && isLive(resolvedStatus(input))) {
    return Math.max(0, now - epochMs(timestamp));
  }
  return null;
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function preview(value: unknown, max = 48): string {
  if (value === undefined || value === null) return "";
  let raw: string;
  if (typeof value === "string") raw = value;
  else {
    try { raw = JSON.stringify(value); } catch { raw = String(value); }
  }
  const oneLine = raw.replace(/\s+/g, " ").trim();
  if (oneLine === "null") return "";
  return oneLine.length > max ? `${oneLine.slice(0, max)}…` : oneLine;
}

export function countRuntimeSteps(tree?: RuntimeTreeSummary | null): number {
  if (!tree) return 0;
  return 1 + (tree.children || []).reduce((sum, child) => sum + countRuntimeSteps(child), 0);
}

function workflowPayload(output: unknown): Record<string, unknown> | null {
  if (output && typeof output === "object" && !Array.isArray(output)) {
    return output as Record<string, unknown>;
  }
  if (typeof output !== "string") return null;
  try {
    const parsed = JSON.parse(output);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function resolvedStatus(input: RuntimeSummaryInput): string {
  const payload = workflowPayload(input.tree?.output);
  const outer = (input.status || input.tree?.status || "").toLowerCase();
  if (isLive(outer)) return outer;

  const inner = String(payload?.status || "").toLowerCase();
  const outerIsGeneric = ["", "completed", "done", "success"].includes(outer);
  if (outerIsGeneric && input.fnName === "gui_agent") {
    return GUI_OUTCOMES.has(inner) ? inner : "error";
  }
  return outer;
}

/** Render the persisted backend handoff; this layer never starts a model call. */
export function runtimeAnswer(input: RuntimeSummaryInput): string | null {
  if (isLive(resolvedStatus(input))
    || resolvedStatus(input) === "paused") return null;
  const output = input.tree?.output;
  if (input.fnName === "gui_agent" && resolvedStatus(input) === "cancelled"
    && typeof output === "string" && output.trim() === "null") return null;
  if (output === undefined || output === null || output === "") return input.tree?.error || null;
  const payload = workflowPayload(output);
  if (payload && typeof payload.summary === "string") {
    return [payload.summary.trim(), typeof payload.handoff_instruction === "string"
      ? payload.handoff_instruction.trim() : ""].filter(Boolean).filter((part, i, all) => all.indexOf(part) === i).join("\n\n") || null;
  }
  if (typeof output === "string" && !payload) return output;
  if (typeof output === "number" || typeof output === "boolean") return String(output);
  return "```json\n" + JSON.stringify(payload ?? output, null, 2) + "\n```";
}

export function runtimeSummaryLabel(input: RuntimeSummaryInput): string {
  const text = input.text ?? ((en: string) => en);
  const rawStatus = resolvedStatus(input);
  const running = isLive(rawStatus);
  const cancelled = rawStatus === "cancelled" || rawStatus === "canceled";
  const interrupted = rawStatus === "interrupted";
  const paused = rawStatus === "paused";
  const capped = rawStatus === "capped";
  const guiFailed = input.fnName === "gui_agent" && rawStatus === "failed";
  const guiInfeasible = input.fnName === "gui_agent" && rawStatus === "infeasible";
  const guiSucceeded = input.fnName === "gui_agent" && rawStatus === "succeeded";
  const errored = rawStatus === "error"
    || (Boolean(input.tree?.error) && !(running || cancelled || interrupted || paused || capped))
    || (rawStatus === "failed" && !guiFailed);
  const statusByResult: Record<string, string> = {
    cancelling: text("Cancelling…", "正在取消"),
    pending: text("Running…", "运行中…"),
    running: text("Running…", "运行中…"),
    streaming: text("Running…", "运行中…"),
    paused: text("Paused", "已暂停"),
    cancelled: text("Cancelled", "已取消"),
    canceled: text("Cancelled", "已取消"),
    interrupted: text("Interrupted", "已中断"),
    capped: text("Stopped", "已停止"),
  };
  const status = rawStatus === "paused" && input.fnName === "gui_agent"
    ? text("Paused", "已暂停")
    : errored
    ? text("Error", "出错")
    : guiFailed
      ? text("Failed", "失败")
      : guiInfeasible
        ? text("Needs takeover", "需要接手")
        : guiSucceeded
          ? text("Succeeded", "成功")
          : statusByResult[rawStatus] || text("Completed", "已完成");
  const steps = countRuntimeSteps(input.tree);
  const payload = input.fnName === "gui_agent"
    ? workflowPayload(input.tree?.output)
    : null;
  const guiPreview = input.fnName === "gui_agent"
    ? preview(guiInfeasible
      ? payload?.handoff_instruction || payload?.summary
      : payload?.summary)
    : "";
  const stepCount = text(
    `${steps} ${steps === 1 ? "step" : "steps"}`,
    `${steps} 步`,
  );
  let result = stepCount;
  if (running) {
    result = steps ? stepCount : text("Starting", "正在启动");
  } else if (errored) {
    result = preview(input.tree?.error) || preview(input.tree?.output) || stepCount;
  } else if (guiInfeasible || guiFailed || guiSucceeded) {
    result = guiPreview || stepCount;
  } else if (paused) {
    result = stepCount;
  } else if (!(cancelled || interrupted || capped)) {
    result = (payload ? stepCount : preview(input.tree?.output)) || stepCount;
  }
  const elapsed = durationMs(input);
  return [input.fnName, status, elapsed === null ? "" : formatDuration(elapsed), result]
    .filter(Boolean)
    .join(" · ");
}
