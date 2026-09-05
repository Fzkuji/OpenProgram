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

export type RuntimeConclusion = {
  label: string;
  meta: string;
  summary: string;
  result?: string;
  tone: "success" | "error" | "cancelled";
};

const RUNNING = new Set(["pending", "running", "streaming", "cancelling"]);
const WORKFLOW_FNS = new Set(["auto_workflow", "agentic_workflow"]);
const GUI_OUTCOMES = new Set(["succeeded", "failed", "infeasible", "cancelled"]);

function isWorkflowName(fnName: string): boolean {
  return WORKFLOW_FNS.has(fnName);
}

function epochMs(value: number): number {
  return value > 1e12 ? value : value * 1000;
}

function durationMs({ status, timestamp, now = Date.now(), tree }: RuntimeSummaryInput): number | null {
  if (Number.isFinite(tree?.duration_ms)) return Math.max(0, tree!.duration_ms!);
  if (tree?.start_time && tree.end_time) {
    return Math.max(0, epochMs(tree.end_time) - epochMs(tree.start_time));
  }
  if (timestamp && RUNNING.has((status || "").toLowerCase())) {
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
  const outer = (input.status || input.tree?.status || "").toLowerCase();
  if (RUNNING.has(outer)) return outer;

  const payload = workflowPayload(input.tree?.output);
  const inner = String(payload?.status || "").toLowerCase();
  const outerIsGeneric = ["", "completed", "done", "success"].includes(outer);
  if (outerIsGeneric && input.fnName === "gui_agent") {
    return GUI_OUTCOMES.has(inner) ? inner : "error";
  }
  if (!isWorkflowName(input.fnName)) return outer;
  if (
    outerIsGeneric
    && ["capped", "interrupted", "cancelled", "canceled", "failed", "error"].includes(inner)
  ) {
    return inner;
  }
  return outer || inner;
}

function counted(noun: string, count: number): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** Render the persisted backend handoff; this layer never starts a model call. */
export function runtimeAnswer(input: RuntimeSummaryInput): string | null {
  if (isWorkflowName(input.fnName) || RUNNING.has(resolvedStatus(input))) return null;
  const output = input.tree?.output;
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

export function runtimeConclusion(input: RuntimeSummaryInput): RuntimeConclusion | null {
  if (!isWorkflowName(input.fnName)) return null;
  const text = input.text ?? ((en: string) => en);
  const rawStatus = resolvedStatus(input);
  if (RUNNING.has(rawStatus)) return null;

  const payload = workflowPayload(input.tree?.output);
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const revisions = Array.isArray(payload?.revisions) ? payload.revisions.length : 0;
  const succeeded = items.filter((item) =>
    item && typeof item === "object"
    && String((item as Record<string, unknown>).status || "").toLowerCase() === "completed"
  ).length;
  const failedCalls = items.filter((item) => {
    if (!item || typeof item !== "object") return false;
    const status = String((item as Record<string, unknown>).status || "").toLowerCase();
    return status === "failed" || status === "error";
  }).length;
  const isHandoff = payload?.summary_kind === "workflow_handoff_v1";
  const summary = isHandoff && typeof payload?.summary === "string"
    ? payload.summary.trim()
    : "";
  let directResult = "";
  if (isHandoff && payload?.return_result === true && payload.result != null) {
    directResult = typeof payload.result === "string"
      ? payload.result.trim()
      : JSON.stringify(payload.result, null, 2);
  }
  const cancelled = rawStatus === "cancelled" || rawStatus === "canceled";
  const interrupted = rawStatus === "interrupted";
  const failed = rawStatus === "error" || rawStatus === "failed" || Boolean(input.tree?.error);
  const capped = rawStatus === "capped";

  if (cancelled) {
    return {
      label: text("Conclusion", "结论"),
      meta: items.length
        ? text(
            `Workflow was cancelled after ${counted("recorded call", items.length)}.`,
            `工作流已取消；取消前记录了 ${items.length} 个调用。`,
          )
        : text("Workflow was cancelled.", "工作流已取消。"),
      summary: "",
      tone: "cancelled",
    };
  }

  if (interrupted) {
    return {
      label: text("Conclusion", "结论"),
      meta: text("Workflow was interrupted.", "工作流已中断。"),
      summary: String(input.tree?.error || summary || "").trim(),
      tone: "error",
    };
  }

  if (failed || capped) {
    return {
      label: text("Conclusion", "结论"),
      meta: capped
        ? text(
            items.length
              ? `Workflow stopped at its execution limit after ${counted("recorded call", items.length)}.`
              : "Workflow stopped at its execution limit.",
            items.length
              ? `工作流达到执行上限后停止，共记录 ${items.length} 个调用。`
              : "工作流达到执行上限后停止。",
          )
        : items.length
          ? text(
              `Workflow failed after ${counted("recorded call", items.length)}.`,
              `工作流在记录 ${items.length} 个调用后失败。`,
            )
          : text("Workflow failed.", "工作流失败。"),
      summary: String(input.tree?.error || summary || "").trim(),
      tone: "error",
    };
  }

  const repair = revisions
    ? text(
        `, with ${counted("automatic repair", revisions)}`,
        `，经过 ${revisions} 次自动修复`,
      )
    : "";
  return {
    label: text("Conclusion", "结论"),
    meta: items.length
      ? text(
          `Completed ${counted("call", items.length)}: ${succeeded} succeeded, ${failedCalls} failed${repair}.`,
          `已完成 ${items.length} 个调用：${succeeded} 个成功、${failedCalls} 个失败${repair}。`,
        )
      : text("Workflow completed.", "工作流已完成。"),
    summary,
    ...(directResult ? { result: directResult } : {}),
    tone: "success",
  };
}

export function runtimeSummaryLabel(input: RuntimeSummaryInput): string {
  const text = input.text ?? ((en: string) => en);
  const rawStatus = resolvedStatus(input);
  const running = RUNNING.has(rawStatus);
  const cancelled = rawStatus === "cancelled" || rawStatus === "canceled";
  const interrupted = rawStatus === "interrupted";
  const capped = rawStatus === "capped";
  const guiFailed = input.fnName === "gui_agent" && rawStatus === "failed";
  const guiInfeasible = input.fnName === "gui_agent" && rawStatus === "infeasible";
  const guiSucceeded = input.fnName === "gui_agent" && rawStatus === "succeeded";
  const errored = rawStatus === "error"
    || (Boolean(input.tree?.error) && !(running || cancelled || interrupted || capped))
    || (rawStatus === "failed" && !guiFailed);
  const statusByResult: Record<string, string> = {
    cancelling: text("Cancelling…", "正在取消"),
    pending: text("Running…", "运行中…"),
    running: text("Running…", "运行中…"),
    streaming: text("Running…", "运行中…"),
    cancelled: text("Cancelled", "已取消"),
    canceled: text("Cancelled", "已取消"),
    interrupted: text("Interrupted", "已中断"),
    capped: text("Stopped", "已停止"),
  };
  const status = errored
    ? text("Error", "出错")
    : guiFailed
      ? text("Failed", "失败")
      : guiInfeasible
        ? text("Needs takeover", "需要接手")
        : guiSucceeded
          ? text("Succeeded", "成功")
          : statusByResult[rawStatus] || text("Completed", "已完成");
  const steps = countRuntimeSteps(input.tree);
  const payload = isWorkflowName(input.fnName) || input.fnName === "gui_agent"
    ? workflowPayload(input.tree?.output)
    : null;
  const handoffPreview = payload?.summary_kind === "workflow_handoff_v1"
    ? preview(payload.summary)
    : "";
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
  } else if (!(cancelled || interrupted || capped)) {
    result = handoffPreview
      || (payload ? stepCount : preview(input.tree?.output))
      || stepCount;
  }
  const elapsed = durationMs(input);
  return [input.fnName, status, elapsed === null ? "" : formatDuration(elapsed), result]
    .filter(Boolean)
    .join(" · ");
}
