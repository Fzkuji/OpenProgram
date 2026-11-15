/** One dictionary for the Activity pages: the same run gets the same word
 *  and the same colour everywhere. Pure, so the node tests can load it. */
import type { ActivityAttemptEvent, ActivityRun, ActivityRunEvent } from "../../../lib/net/execution/activity-runs-client";
import type { ActivityTone, Translate } from "./activity-page-types";
import { presentTool } from "../../chat/messages/execution/tool-presentation.ts";
import { readableRequest } from "../debugger/debugger-presentation.ts";

/** A run keeps its row when its reply arrives and its id changes to the message's. */
export const runRowId = (run: ActivityRun) => run.execution_id ?? run.id;

export type ActivityRunOutcome = "running" | "waiting" | "paused" | "completed" | "nothing-new" | "check-failed" | "skipped" | "missed" | "failed" | "cancelled" | "interrupted" | "unknown";

/** How a run ended. A completed execution still reads its reply: a check
 *  that could not check finishes normally and says so in a tag. */
export function runOutcome(run: ActivityRun, waiting: boolean): ActivityRunOutcome {
  if (waiting) return "waiting";
  switch (run.execution_status) {
    case "queued": case "running": case "pausing": case "cancelling": return "running";
    case "paused": return "paused";
    case "failed": return "failed";
    case "cancelled": return "cancelled";
    case "interrupted": case "reconciliation_required": return "interrupted";
  }
  if (run.task_outcome === "failed") return "check-failed";
  if (run.task_outcome === "missed") return "missed";
  if (run.task_outcome === "quiet") return "nothing-new";
  if (run.task_outcome === "skipped") return "skipped";
  if (run.task_outcome === "result") return "completed";
  const reply = run.reply.trim();
  if (/^\[could not check\]/i.test(reply)) return "check-failed";
  if (/^\[error\]/i.test(reply)) return "failed";
  // With no end saved, what the message says about stopping outranks its tag.
  if (run.message_status === "paused" && run.ended_at === null) return "paused";
  if (/^\[nothing new\]$/i.test(reply)) return "nothing-new";
  if (run.execution_status === "completed") return "completed";
  switch (run.message_status) {
    case "paused": return "paused";
    case "failed": case "error": return "failed";
    case "cancelled": return "cancelled";
    case "interrupted": return "interrupted";
    case "completed": case "success": case "done": return "completed";
  }
  return run.ended_at !== null ? "completed" : "unknown";
}

export function durationWords(seconds: number, text: Translate): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return text(`${total}s`, `${total} 秒`);
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return text(`${minutes}m ${total % 60}s`, `${minutes} 分 ${total % 60} 秒`);
  return text(`${Math.floor(minutes / 60)}h ${minutes % 60}m`, `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`);
}

const pad = (value: number) => String(value).padStart(2, "0");

/** `HH:MM` on a run, `HH:MM:SS` inside one. */
export function clockTime(seconds: number, withSeconds = false): string {
  const date = new Date(seconds * 1000);
  const minutes = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return withSeconds ? `${minutes}:${pad(date.getSeconds())}` : minutes;
}

export function dayName(seconds: number, text: Translate, nowSeconds = Date.now() / 1000): string {
  const day = new Date(seconds * 1000); day.setHours(0, 0, 0, 0);
  const today = new Date(nowSeconds * 1000); today.setHours(0, 0, 0, 0);
  const difference = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (difference === 0) return text("Today", "今天");
  if (difference === 1) return text("Yesterday", "昨天");
  return day.toLocaleDateString([], { month: "short", day: "numeric", ...(day.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
}

/** A stored reply in the reader's words: its tag becomes the status, and an
 *  empty one says so. */
export function cleanRunTitle(value: string | null | undefined, text: Translate): string {
  const cleaned = String(value ?? "").trim()
    .replace(/^\[nothing new\]$/i, text("Nothing new", "无新内容"))
    .replace(/^\[(could not check|error)\]\s*/i, "")
    .replace(/\*\*/g, "");
  return cleaned || text("No reply saved", "没有保存回复");
}

export const firstLine = (value: string) => (value.split("\n").find(line => line.trim()) ?? "").replace(/\(\[[^\]]*\]\([^)]*\)\)/g, "").trim();

/** A run's title: the first line of its reply, else what was asked. A report
 *  with no saved text has only what `reportName` knows it by. */
export function runTitle(run: ActivityRun, text: Translate, reportName?: string | null): string {
  if (run.reply.trim()) return firstLine(cleanRunTitle(run.reply, text));
  if (run.is_report) return reportName?.trim() || text("Empty report", "空报告");
  if (run.task_text?.trim()) return firstLine(cleanRunTitle(run.task_text, text));
  if (run.asked_preview.trim()) return readableRequest(firstLine(run.asked_preview), text);
  return text("No reply saved", "没有保存回复");
}

export interface ActivityRunLook { label: string; tone: ActivityTone; markTone: ActivityTone }

export interface RunStatusOptions {
  now: number;
  text: Translate;
  /** What it waits on, in words. */
  waitingLabel?: string;
  /** What it is doing now, in words. */
  doing?: string;
}

const look = (label: string, tone: ActivityTone = "muted", markTone: ActivityTone = tone): ActivityRunLook => ({ label, tone, markTone });

/** A run's status line, start time on the left to match the right column. */
export function runStatusLine(run: ActivityRun, outcome: ActivityRunOutcome, options: RunStatusOptions): ActivityRunLook {
  const { now, text } = options;
  const start = run.started_at ?? run.ended_at;
  const hour = start !== null ? clockTime(start) : "";
  if (run.is_report) return look([`${text("Report", "报告")} · ${hour}`, run.reply.trim() ? "" : text("no text saved", "没有保存内容")].filter(Boolean).join(" · "));
  if (outcome === "waiting") return look(options.waitingLabel ?? text("Waiting for your answer", "等你回答"), "warn");
  if (outcome === "running") return look([options.doing ?? text("Running", "正在运行"), run.started_at !== null ? durationWords(now - run.started_at, text) : ""].filter(Boolean).join(" · "), "run");
  if (run.task_outcome === "result" && (!run.reply.trim() || run.message_status === "paused")) return look(`${text("Sent you a message", "已发消息")} · ${text("no text saved", "没有保存内容")}`);
  const took = run.started_at !== null && run.ended_at !== null ? durationWords(run.ended_at - run.started_at, text) : "";
  const when = [hour, took].filter(Boolean).join(" · ");
  const tagged = (word: string) => [word, when].filter(Boolean).join(" · ");
  if (run.reason_code === "wait_declined") return look(tagged(text("Declined", "已拒绝")));
  switch (outcome) {
    case "failed": return look(tagged(text("Failed", "失败")), "err");
    case "check-failed": return look(tagged(text("Check failed", "检查失败")), "warn");
    case "skipped": return look(tagged(text("Skipped: the previous run was still going", "已跳过：上一次运行还没结束")));
    case "missed": return look(tagged(text("Missed: OpenProgram was not running", "错过：当时 OpenProgram 没有运行")));
    case "cancelled": return look(tagged(text("Cancelled", "已取消")));
    case "interrupted": return look(tagged(text("Interrupted", "已中断")));
    case "paused":
      if (run.ended_at === null && !run.execution_id) return look(`${text("Paused", "已暂停")} · ${text("no end saved", "没有保存结束时间")}`);
      return look(tagged(text("Paused", "已暂停")));
  }
  if (run.ended_at === null && !took) return look([hour, text("no end saved", "没有保存结束时间")].filter(Boolean).join(" · "));
  return look(when);
}

export interface ActivityAttemptLook { label: string; tone: ActivityTone; state: "running" | "paused" | "completed" | "cancelled" | "interrupted" | "failed" | "ended" }

/** An attempt that stopped at a saved point it can continue from. */
const PAUSED_ATTEMPTS = new Set(["restart_recoverable", "waiting_at_safe_point", "step_at_safe_point", "owner_lost_after_checkpoint"]);
/** An attempt cut short by something other than its own work failing. */
const INTERRUPTED_ATTEMPTS = new Set(["restart_pending", "reconciliation_required"]);

export function attemptLabel(event: Pick<ActivityAttemptEvent, "number" | "status" | "outcome">, text: Translate): ActivityAttemptLook {
  const number = event.number;
  const outcome = (event.outcome ?? "").toLowerCase();
  const says = (word: string) => `${word} · ${text(`Attempt ${number}`, `第 ${number} 次尝试`)}`;
  if (event.status !== "ended") return { label: says(text("Running", "正在运行")), tone: "run", state: "running" };
  if (outcome.includes("paus") || PAUSED_ATTEMPTS.has(outcome)) return { label: says(text("Paused", "已暂停")), tone: "muted", state: "paused" };
  if (outcome === "completed" || outcome === "succeeded") return { label: says(text("Completed", "已完成")), tone: "muted", state: "completed" };
  if (outcome.includes("cancel")) return { label: says(text("Cancelled", "已取消")), tone: "muted", state: "cancelled" };
  if (outcome.includes("interrupt") || outcome.startsWith("owner_lost") || INTERRUPTED_ATTEMPTS.has(outcome)) return { label: says(text("Interrupted", "已中断")), tone: "muted", state: "interrupted" };
  if (outcome.includes("fail") || outcome.includes("error")) return { label: says(text("Failed", "失败")), tone: "err", state: "failed" };
  return { label: says(text("Ended", "已结束")), tone: "muted", state: "ended" };
}

/** A browser step by what it did: `Browser: navigate gomami.io, evaluate`. */
function browserStepTitle(input: unknown, result: string | null | undefined, text: Translate): string | null {
  let request: unknown = input;
  if (typeof input === "string") {
    try { request = JSON.parse(input); } catch { return null; }
  }
  const fields = (request && typeof request === "object" ? request : {}) as { action?: unknown; args?: unknown; actions?: unknown };
  const actions = (Array.isArray(fields.actions) ? fields.actions : typeof fields.action === "string" ? [fields] : []) as { action?: unknown; args?: { url?: unknown } }[];
  const words = actions.map(item => {
    const host = typeof item.args?.url === "string" ? item.args.url.replace(/^https?:\/\//, "").split("/")[0] : "";
    return [typeof item.action === "string" ? item.action : "", host].filter(Boolean).join(" ");
  }).filter(Boolean);
  if (words.length) return `${text("Browser", "浏览器")}: ${words.join(", ")}`;
  return result?.startsWith("<guide") ? `${text("Browser", "浏览器")}: ${text("guide", "指南")}` : null;
}

/** A tool step in the words the chat uses for it. */
export function stepTitle(name: string, input: unknown, text: Translate, result?: string | null): string {
  const browser = name === "computer_use_agent" ? browserStepTitle(input, result, text) : null;
  if (browser) return browser;
  const raw = typeof input === "string" ? input : input == null ? undefined : JSON.stringify(input);
  const presented = presentTool(name, raw, text);
  return [presented.title, presented.target].filter(Boolean).join(" ");
}

/** One event row's line: the state word first, so an ellipsis never hides it. */
export function eventLabel(event: ActivityRunEvent, text: Translate): { label: string; tone: ActivityTone } {
  switch (event.kind) {
    case "asked": return { label: `${event.by === "check-in" ? text("Check-in", "巡检") : text("Asked", "提问")}: ${readableRequest(event.text, text)}`, tone: "muted" };
    case "step": {
      const title = stepTitle(event.name, event.input, text, event.result);
      return { label: event.failed ? `${text("Failed", "失败")} · ${title}` : title, tone: event.failed ? "err" : "muted" };
    }
    case "replied": return { label: `${text("Replied", "回复")}: ${cleanRunTitle(event.text, text)}`, tone: "muted" };
    case "attempt": { const { label, tone } = attemptLabel(event, text); return { label, tone }; }
    case "subagent": return { label: event.name, tone: event.failed ? "err" : "muted" };
  }
}
