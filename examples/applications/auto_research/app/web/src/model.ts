// What the backend returns, and how the page reads it.
export interface Run { status: string; at: string; inputs?: Record<string, string>; commit?: string; report?: string; run?: string }
export interface Task {
  name: string; title: string; phase: string; gates: string[]; artifacts: string[];
  inputs: string[]; required: string[]; workflow: string | null; state: string;
  blocked_by?: string; problems: string[]; next: string; done?: string; last_runs: Run[];
  /** Kinds of work the task serves; empty serves every kind. */
  kinds: string[];
  /** When it passes, for the author; how its agent works; the file the author approves. */
  goal: string; brief: string; approves: string;
  /** The sidebar group within the phase, and the task's place in the sidebar's order. */
  group: string; order: number;
}
/** A file shown beside a conversation (backend `artifacts`). */
export interface Artifact {
  path: string; kind: "image" | "pdf" | "table" | "markdown" | "html" | "text" | "other";
  group: "changed" | "paper" | "deliverables" | "outputs"; mtime: number; size: number;
}
/** A change a run proposes to a project file (its edits.json). */
export interface Edit {
  id: string; file: string; section: string; kind: string; problem: string; quote: string; replacement: string;
  /** pending: can be applied; stale: the text it changes is gone. */
  state: "pending" | "applied" | "rejected" | "stale";
}
export interface Question { id: string; title: string; detail: string; options: { id: string; label: string }[]; answer: string }
export interface Made { name: string; path: string; kind: string; place: string; placed: boolean }
/** The newest run of a task and what is left to do about it (backend `result`). */
export interface Result {
  task: string; title: string; run: string; at: string; status: string; verdict: string; report: string;
  edits: Edit[]; questions: Question[]; files: Made[]; set_aside: boolean; pending: boolean;
  todo: { edits: number; questions: number; places: number; fix: number };
}
export interface PastRun { run: string; at: string; status: string; verdict: string; report: string }
/** An uncommitted change of the project (backend `changes`); by is the task it came from. */
export interface Change { path: string; state: "new" | "modified" | "deleted"; added: number; removed: number; by: string }

export const GROUPS: Record<Artifact["group"], string> = { changed: "刚改", paper: "论文", deliverables: "交付物", outputs: "检查输出" };

export interface Overview {
  project: string; head: string; branch: string; dirty: string[]; tasks: Task[];
  sections: string[]; sections_error?: string; phases: { id: string; title: string }[]; paper_pdf: string;
  brief: Brief; kinds: Kind[]; suggestion: Suggestion; progress: Progress | Record<string, never>; deadline: string;
  spend: Spend; preregistration: Preregistration;
  /** Per claim, how many works support, contradict or only relate to it (claim_support.csv). */
  consensus: Record<string, { supports: number; contradicts: number; related: number }>;
}

/** The experiment plan as the author locked it before the results (workflow/preregistration.json). */
export interface Preregistration {
  locked: boolean; at?: string;
  changed: { claim: string; experiment: string; kind: string }[];
  exploratory: { claim: string; experiment: string; kind: string }[];
}

/** What the project's model calls have cost, from every saved run. */
export interface Spend { runs: number; calls: number; cost: number; by_workflow: { workflow: string; runs: number; cost: number }[] }
/** A dollar amount as the overview shows it. */
export const dollars = (n: number) => (n >= 10 ? "$" + n.toFixed(0) : n >= 0.01 ? "$" + n.toFixed(2) : "不到 $0.01");

/** The paper's kind of work and its claim (workflow/project.md). */
export interface Brief { kind: string; claim: string }
export interface Kind { id: string; title: string; about: string }
/** The reader's suggestion of the kind; empty until it has read the paper. */
export interface Suggestion { kind?: string; reason?: string; basis?: string; claim?: string; at?: string }

export type Stand = "done" | "doing" | "todo";
export interface Reading {
  task: string; state: Stand; source: "check" | "agent" | "author";
  evidence: string; missing: string; approval: boolean;
}
/** Where each task stands (workflow/progress.json), with the files changed since. */
export interface Progress { kind: string; at: string; head: string; confirmed: boolean; tasks: Reading[]; changed: string[] }

export const STAND: Record<Stand, string> = { done: "已完成", doing: "进行中", todo: "没开始" };
export const SOURCE: Record<Reading["source"], string> = { check: "检查", agent: "agent", author: "你改的" };

/** Whether a task is listed for the kind of work; every task is until a kind is set. */
export const serves = (task: Task, kind: string) => !kind || !task.kinds.length || task.kinds.includes(kind);

/** The dot a task shows: its reading when there is one, else its check. */
export type Dot = Stand | "wait" | "next";
export function readingOf(view: Overview, name: string): Reading | undefined {
  return "tasks" in view.progress ? view.progress.tasks.find((r) => r.task === name) : undefined;
}

/** Up to three tasks to do next: what waits on the author first, then what is under way, then the first not begun. */
export function nextTasks(view: Overview): { task: Task; reading: Reading }[] {
  if (!("tasks" in view.progress)) return [];
  const rank = (r: Reading) => (r.state === "doing" && r.approval ? 0 : r.state === "doing" ? 1 : 2);
  const order = new Map(view.tasks.map((t, i) => [t.name, i]));
  return view.progress.tasks.filter((r) => r.state !== "done")
    .sort((a, b) => rank(a) - rank(b) || (order.get(a.task) ?? 0) - (order.get(b.task) ?? 0))
    .slice(0, 3)
    .map((reading) => ({ task: view.tasks.find((t) => t.name === reading.task)!, reading }))
    .filter((x) => x.task);
}

export function dotOf(view: Overview, name: string, next: string[]): Dot | undefined {
  const r = readingOf(view, name);
  if (!r) return undefined;
  if (r.state === "doing" && r.approval) return "wait";
  if (r.state === "todo" && next.includes(name)) return "next";
  return r.state;
}

/** Days left until a date the rules give, or null. */
export function daysLeft(date: string) {
  const t = Date.parse(date);
  return Number.isNaN(t) ? null : Math.ceil((t - Date.now()) / 86400000);
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
