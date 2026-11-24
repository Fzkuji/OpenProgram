// What the backend returns, and how the page reads it.
export interface Run { status: string; at: string; inputs?: Record<string, string>; commit?: string; report?: string; run?: string }
export interface Task {
  name: string; title: string; phase: string; gates: string[]; artifacts: string[];
  inputs: string[]; required: string[]; workflow: string | null; state: string;
  blocked_by?: string; problems: string[]; next: string; done?: string; last_runs: Run[];
}
/** A file shown beside a conversation (backend `artifacts`). */
export interface Artifact {
  path: string; kind: "image" | "pdf" | "table" | "markdown" | "html" | "text" | "other";
  group: "changed" | "paper" | "deliverables" | "outputs"; mtime: number; size: number;
}
export const GROUPS: Record<Artifact["group"], string> = { changed: "刚改", paper: "论文", deliverables: "交付物", outputs: "检查输出" };

export interface Overview {
  project: string; head: string; branch: string; dirty: string[]; tasks: Task[];
  sections: string[]; sections_error?: string; phases: { id: string; title: string }[]; paper_pdf: string;
}

export const PHASES: [string, string][] = [["research", "研究"], ["writing", "写作"], ["release", "发布"]];

export type Shown = "pass" | "fail" | "blocked" | "never";
export const SHOWN: Record<Shown, string> = { pass: "通过", fail: "有问题", blocked: "被挡住", never: "没跑过" };

/** One state per task for the sidebar: a failing gate first, then the last run. */
export function shown(task: Task): Shown {
  if (task.state === "blocked") return "blocked";
  const last = task.last_runs[0];
  if (!last) return "never";
  if (last.status === "FAIL") return "fail";
  if (last.status === "BLOCKED") return "blocked";
  return "pass";
}

export const RESULT: Record<string, string> = { PASS: "通过", PASS_WITH_NOTES: "通过（有提示）", FAIL: "未通过", BLOCKED: "被挡住" };

const STAGES: Record<string, string> = {};
export const stageLabel = (stage: string) =>
  STAGES[stage] || (stage.startsWith("gate ") ? `前置条件：${stage.slice(5)}` : stage.startsWith("run ") ? "开始" : stage);

export const baseName = (path: string) => path.split("/").filter(Boolean).pop() || path;
export function tildePath(path: string, home: string) {
  return home && path.startsWith(home + "/") ? "~" + path.slice(home.length) : path;
}
export function initials(name: string) {
  const parts = name.replace(/[^A-Za-z0-9一-鿿]+/g, " ").trim().split(" ").filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
export function ago(at: string) {
  const t = Date.parse(at.replace(" ", "T"));
  if (!t) return at;
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 天前`;
  return new Date(t).toLocaleDateString();
}
