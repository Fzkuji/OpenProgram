/** What a time chart draws, with every block's height at each level of
 *  detail. An Agent's chart: topics, the messages in them, each message's
 *  steps and what is inside a step. The conversation's chart has the same
 *  columns over every branch, titles each message by the task it worked on,
 *  colours everything after the topics by the agent that did it, and goes one column deeper for each sub-agent a sub-agent started.
 *  Pure, so the node tests can load it. */
import type { ActivityRun, ActivityRunEvent, ActivitySubAgentEvent } from "../../../../lib/net/execution/activity-runs-client";
import type { Translate } from "../pages/activity-page-types";
import { attemptLabel, cleanRunTitle, clockTime, durationWords, eventLabel, firstLine, runOutcome, runRowId, runTitle, stepTitle, type ActivityRunOutcome } from "../pages/activity-run-wording.ts";
import { readableRequest } from "../../debugger/debugger-presentation.ts";

export const COLUMN_WIDTH = 256;
export const HEADER_HEIGHT = 30;
export const LEVELS = 4;

/** The main agent's colour when its branch has none: the conversation's only one. */
export const MAIN_COLOR = "var(--folder-color)";
/** Sub-agents take these in the order they were started, skipping the main
 *  agents' own: branch lane colours (app/styles/design/color.css), in an
 *  order that keeps neighbours apart. */
const SUB_COLORS = [2, 3, 7, 8, 6, 1, 5, 11, 10, 13].map(lane => `var(--lane-${lane})`);
const GROUP_CAP = 120;
const ROOT_HEIGHT = { gap: 32, next: 60, more: 36 } as const;
const IDLE_SECONDS = 6 * 3600;
const MAX_SECONDS = 300;
const ACTIONS_HEIGHT = 28;
const ERROR_HEIGHT = 38;
/** A message's second line. */
const WORDS_HEIGHT = 19;
/** A message's title line: a message with no title yet has none. */
const TITLE_HEIGHT = 21;

/** Each topic's main colour, in the order topics appeared. Neighbours differ
 *  enough for every colour vision, light and dark. */
const TOPIC_HUES = [35, 255, 320, 115, 285, 200, 80, 150];
/** A shade step down the tree: how much lighter, and how far the hue turns. */
const MESSAGE_SHADES = [[0.02, -8], [0.1, 6], [0.05, -3], [0.13, 10]];
const STEP_SHADES = [[0.06, -4], [0.11, 4], [0.085, 0]];
const INSIDE_SHADES = [[0.05, 0], [0.085, 3]];

export interface ChartPaint { order: number | null; hue: number; lift: number; chroma: number }

const oklch = (lightness: number, chroma: number, hue: number) => `oklch(${lightness.toFixed(3)} ${chroma.toFixed(3)} ${hue})`;

/** One colour for both themes; a topic with no place of its own stays grey. */
export function paintColor(paint: ChartPaint): string {
  const odd = (paint.order ?? 0) % 2 === 1;
  const chroma = paint.order === null ? 0 : 0.12 * paint.chroma;
  const light = paint.order === null ? 0.56 : odd ? 0.61 : 0.47;
  const dark = paint.order === null ? 0.62 : odd ? 0.66 : 0.58;
  return `light-dark(${oklch(Math.min(0.84, light + paint.lift), chroma, paint.hue)}, ${oklch(Math.min(0.86, dark + paint.lift), chroma, paint.hue)})`;
}

const shade = (paint: ChartPaint, shades: number[][], index: number, fade: number): ChartPaint => {
  const [lift, turn] = shades[index % shades.length];
  return { ...paint, hue: paint.hue + turn, lift: paint.lift + lift, chroma: paint.chroma * fade };
};

/** The agent a block wears the glyph of. `key` is what the panel knows it
 *  by: a main agent's branch, a sub-agent's job. */
export interface ChartAgent { key: string | null; name: string; color: string; sub: boolean }

export interface ChartNode {
  id: string;
  depth: number;
  /** 0 a topic, 1 a message, 2 a step, 3 and up inside a step. */
  role: number;
  /** Set on one block per agent: a main agent's newest message, and the row that started a sub-agent. */
  agent?: ChartAgent;
  /** The colour of the agent that did this, in the conversation's chart; its bar wears it. */
  tint?: string;
  /** A sub-agent still going, or a message still going or waiting on the reader: its glyph pulses. */
  live?: boolean;
  /** How a message's run stands, which its topic sums up. */
  outcome?: ActivityRunOutcome;
  /** Drawn as room only: a topic the conversation has not named yet. */
  hidden?: boolean;
  /** The main agent's sub-agents are still going: its block offers to stop them. */
  stops?: boolean;
  /** A step whose rows are all single calls: they scroll in a cell of their own, so it stays short. */
  folds: boolean;
  time: string;
  /** Topics and messages have a title; a step is its line alone. */
  title?: string;
  /** A message's second line, between its title and its time: what was
   *  asked when the title is its task's, else what came back. */
  words?: string;
  line: string;
  strong: boolean;
  detail?: string;
  seconds: number;
  paint: ChartPaint;
  kids?: ChartNode[];
  parent: ChartNode | null;
  run?: ActivityRun;
  /** What the block stands for, when it is one event: its details open in place. */
  event?: ActivityRunEvent;
  /** Height of the details opened under the block's line; 0 while closed. */
  extra: number;
  hasActions: boolean;
  /** Height at each level of detail, then where layout last put it. */
  heights: number[];
  top: number;
  height: number;
}

export type ChartRoot =
  | { kind: "topic"; id: string; node: ChartNode }
  | { kind: "gap"; id: string; day: string | null; idle: string | null }
  | { kind: "next"; id: string; time: string; title: string; line: string; strong: boolean }
  | { kind: "more"; id: string; label: string };

export interface ChartOptions {
  text: Translate;
  now: number;
  waitOf: (run: ActivityRun) => { waitLabel: string; question: string; answerLabel?: string } | null;
  /** What a run's reply is doing while it goes, as the conversation streams it. */
  livePhase?: (run: ActivityRun) => LivePhase | null;
  hasActions: (run: ActivityRun) => boolean;
  /** Calls under way that have no saved step yet. */
  liveCalls: (run: ActivityRun) => { id: string; at: number; label: string }[];
  reportName: (run: ActivityRun) => string | null;
  nextWake: { at: number; line: string; strong?: boolean } | null;
  earlier: number;
}

/** What a reply under way is doing when no call is running. */
/** What a reply under way is doing; a tool it runs is named by its command, or else by the step's own words. */
export type LivePhase = { kind: "thinking" } | { kind: "writing" } | { kind: "retrying"; attempt: number; max?: number }
  | { kind: "tool"; command?: string; step?: string };

interface Topic { key: string; title: string; order: number | null; hidden?: boolean }

const makeNode = (values: Pick<ChartNode, "id" | "depth" | "time" | "line" | "seconds" | "parent"> & Partial<ChartNode>): ChartNode => ({
  strong: false, paint: { order: null, hue: 0, lift: 0, chroma: 1 }, hasActions: false, folds: false, extra: 0, heights: [], top: 0, height: 0,
  // A block one column right of its parent is one role further in.
  role: values.parent ? values.parent.role + values.depth - values.parent.depth : values.depth, ...values,
});

const rowHeight = (node: ChartNode): number => {
  if (node.role <= 0) return 60;
  const seconds = Math.min(node.seconds, MAX_SECONDS);
  // A message is as tall as its lines: how long it took is written on its last one.
  if (node.role === 1) return 60 - (node.title ? 0 : TITLE_HEIGHT) + (node.words ? WORDS_HEIGHT : 0) + (node.hasActions ? ACTIONS_HEIGHT : 0) + (node.stops ? ACTIONS_HEIGHT : 0) + node.extra;
  // Opened details say the error in full, so they stand in for its two lines.
  return Math.round(34 + Math.sqrt(Math.max(1, seconds)) * 0.8) + (node.extra || (node.detail ? ERROR_HEIGHT : 0));
};

function natural(node: ChartNode, level: number): number {
  if (node.depth >= level || !node.kids) return rowHeight(node);
  const sum = node.kids.reduce((total, kid) => total + natural(kid, level), 0);
  if (!node.folds) return Math.max(rowHeight(node), sum);
  // A step that folds makes room for the details opened inside it.
  const opened = node.kids.reduce((total, kid) => total + kid.extra, 0);
  return Math.max(rowHeight(node), Math.min(GROUP_CAP + opened, sum));
}

function assign(node: ChartNode, height: number, level: number) {
  node.heights[level] = height;
  if (!node.kids) return;
  // Rows that scroll inside their own cell keep their size.
  if (node.folds && level > node.depth) { node.kids.forEach(kid => assign(kid, natural(kid, level), level)); return; }
  const basis = node.kids.map(kid => natural(kid, node.depth < level ? level : node.depth + 1));
  const sum = basis.reduce((total, value) => total + value, 0);
  node.kids.forEach((kid, index) => assign(kid, node.depth < level ? basis[index] + (index === 0 ? height - sum : 0) : height * basis[index] / sum, level));
}

/** The bar's colour: the agent's in the conversation's chart, quieter on
 *  what it did inside a message; a topic's shade in an Agent's own chart. */
export function barColor(node: ChartNode): string {
  if (!node.tint) return paintColor(node.paint);
  return node.role >= 2 && !node.agent ? `color-mix(in oklab, ${node.tint} 60%, transparent)` : node.tint;
}

interface Item { id: string; at: number; event?: ActivityRunEvent; live?: string }

const listOf = <T,>(value: T[] | undefined): T[] => value ?? [];

/** What goes in the Steps column, oldest first. A lone attempt that went fine says nothing. */
function itemsOf(run: ActivityRun, options: ChartOptions): Item[] {
  const items: Item[] = [];
  listOf(run.steps).forEach((event, index) => {
    if (event.kind === "asked") return;
    if (event.kind === "attempt" && !["failed", "interrupted"].includes(attemptLabel(event, options.text).state)) return;
    items.push({ id: `${runRowId(run)}:${event.kind === "step" ? event.node_id : `${event.kind}:${index}`}`, at: event.at, event });
  });
  for (const call of options.liveCalls(run)) items.push({ id: `${runRowId(run)}:live:${call.id}`, at: call.at, live: call.label });
  return items.sort((a, b) => a.at - b.at);
}

const NOUNS: Record<string, [string, string]> = {
  read: ["files", "个文件"], read_file: ["files", "个文件"], edit: ["files", "个文件"], edit_file: ["files", "个文件"], write: ["files", "个文件"], write_file: ["files", "个文件"], apply_patch: ["files", "个文件"],
  bash: ["commands", "条命令"], shell: ["commands", "条命令"], exec_command: ["commands", "条命令"],
};

function groupLine(name: string, input: unknown, count: number, text: Translate): string {
  const verb = stepTitle(name, input, text).split(/\s+/)[0] || name;
  const [english, chinese] = NOUNS[name] ?? ["calls", "次调用"];
  return `${verb} · ${text(`${count} ${english}`, `${count} ${chinese}`)}`;
}

const errorOf = (value: string | null | undefined): string | undefined => {
  const cleaned = (value ?? "").trim();
  return cleaned ? cleaned : undefined;
};

/** How the conversation's chart draws sub-agents: each in a colour of its
 *  own, with what it did one column on, however deep that goes. An Agent's
 *  own chart has none of it and stops at its fourth column. */
interface SubLooks { colorOf: (event: ActivitySubAgentEvent) => string; now: number }

function eventRow(item: Item, end: number, depth: number, parent: ChartNode, text: Translate, looks: SubLooks | null = null): ChartNode {
  const { id, at, event } = item;
  const base = { id, depth, parent, time: clockTime(at, true), seconds: Math.max(0, end - at) };
  if (!event) return makeNode({ ...base, line: `${text("Running", "正在运行")} ${item.live}`, strong: true });
  const { label } = eventLabel(event, text);
  if (event.kind === "step") return makeNode({ ...base, event, line: label, strong: event.failed, detail: event.failed ? errorOf(event.result) : undefined });
  if (event.kind === "attempt") return makeNode({ ...base, event, line: label, strong: true, detail: errorOf(event.failure_text ?? event.reason_code) });
  if (event.kind === "subagent") return subAgentRow(event, id, end, depth, parent, text, looks);
  return makeNode({ ...base, event, line: label });
}

function subAgentRow(event: ActivitySubAgentEvent, id: string, end: number, depth: number, parent: ChartNode, text: Translate, looks: SubLooks | null): ChartNode {
  const finished = event.ended_at ?? end;
  const node = makeNode({
    id, depth, parent, event, time: clockTime(event.ended_at ?? event.at, true), seconds: Math.max(0, finished - event.at),
    line: `${event.failed ? `${text("Failed", "失败")} · ` : ""}${text("Sub-agent", "子 Agent")} · ${event.name}`,
    strong: event.failed, detail: event.failed ? errorOf(event.error) : undefined,
  });
  if (looks) {
    // Its glyph says it is a sub-agent: the row is its name and how it went.
    const color = looks.colorOf(event);
    Object.assign(node, { title: event.name, tint: color, agent: { key: event.job_id ?? null, name: event.name, color, sub: true }, ...subAgentLine(event, text, looks.now) });
  }
  const inner: Item[] = [
    ...listOf(event.steps).map((step, index) => ({ id: `${id}:${index}`, at: step.at, event: step })),
    ...event.children.map((child, index) => ({ id: `${id}:agent:${index}`, at: child.at, event: child })),
  ].sort((a, b) => a.at - b.at);
  if ((!looks && node.role >= LEVELS - 1) || inner.length === 0) return node;
  node.kids = inner.map((item, index) => eventRow(item, inner[index + 1]?.at ?? finished, depth + 1, node, text, looks)).reverse();
  return node;
}

/** Calls of one function in a row share a line; each call is in the next column. */
function stepRows(run: ActivityRun, parent: ChartNode, end: number, options: ChartOptions, looks: SubLooks | null): ChartNode[] {
  const { text } = options;
  const items = itemsOf(run, options);
  const endOf = (position: number) => items[position + 1]?.at ?? end;
  const rows: ChartNode[] = [];
  for (let position = 0; position < items.length;) {
    const first = items[position].event;
    let last = position;
    if (first?.kind === "step" && !first.failed) {
      for (let next = position + 1; next < items.length; next += 1) {
        const event = items[next].event;
        if (event?.kind !== "step" || event.failed || event.name !== first.name) break;
        last = next;
      }
    }
    if (last > position && first?.kind === "step") {
      const group = makeNode({ id: `${items[position].id}:group`, depth: parent.depth + 1, parent, time: clockTime(items[last].at, true), seconds: Math.max(0, endOf(last) - items[position].at), line: groupLine(first.name, first.input, last - position + 1, text) });
      group.kids = items.slice(position, last + 1).map((item, index) => eventRow(item, endOf(position + index), group.depth + 1, group, text)).reverse();
      rows.push(group);
    } else {
      rows.push(eventRow(items[position], endOf(position), parent.depth + 1, parent, text, looks));
    }
    position = last + 1;
  }
  return rows.reverse();
}

/** A command as the line names it: the program and what it works on, without its options or what follows it. */
export function shortCommand(label: string): string {
  // A part that only sets up where or how the rest runs is not what runs.
  const parts = label.split(/\s*(?:&&|\|\||;|\|)\s*/).filter(Boolean);
  const first = parts.find(part => !/^(?:cd|pushd|export|source|set|\.)(?:\s|$)/.test(part)) ?? parts[0] ?? label;
  const words = first.split(/\s+/).filter(word => !/^-./.test(word));
  return words.join(" ") || label;
}

/** What a run under way is doing now, in a few words: the newest call
 *  running, else its sub-agents, else its reply. */
function doingNow(run: ActivityRun, options: ChartOptions): string {
  const { text } = options;
  switch (run.execution_status) {
    case "queued": return text("Queued", "排队中");
    case "cancelling": return text("Stopping…", "正在停止…");
    case "pausing": return text("Pausing…", "正在暂停…");
  }
  const phase = options.livePhase?.(run) ?? null;
  if (phase?.kind === "retrying") return phase.max ? text(`Retrying (${phase.attempt}/${phase.max})`, `重试中（${phase.attempt}/${phase.max}）`) : text(`Retrying (${phase.attempt})`, `重试中（第 ${phase.attempt} 次）`);
  const calls = options.liveCalls(run);
  if (calls.length) {
    const newest = calls.reduce((a, b) => (b.at > a.at ? b : a));
    return `${text("Running", "正在运行")} ${shortCommand(newest.label)}`;
  }
  if (phase?.kind === "tool" && phase.command) return `${text("Running", "正在运行")} ${shortCommand(phase.command)}`;
  if (phase?.kind === "tool" && phase.step) return phase.step;
  const subs = listOf(run.steps).filter(event => event.kind === "subagent" && event.ended_at === null && !event.never_started).length;
  if (subs) return text(`Running ${subs} sub-agent${subs > 1 ? "s" : ""}`, `${subs} 个子 Agent 在运行`);
  if (phase?.kind === "writing") return text("Writing the reply", "正在写回复");
  if (phase?.kind === "thinking") return text("Thinking", "正在思考");
  return text("Working", "正在工作");
}

const COMMANDS = new Set(["bash", "shell", "exec_command"]);
const EDITS = new Set(["edit", "edit_file", "write", "write_file", "apply_patch"]);

/** What a finished run did, counted: commands, edits, sub-agents, and how many of them failed. */
function workDone(run: ActivityRun, text: Translate): string {
  let commands = 0, edits = 0, subs = 0, failed = 0;
  for (const event of listOf(run.steps)) {
    if (event.kind === "step") {
      if (COMMANDS.has(event.name)) commands += 1;
      else if (EDITS.has(event.name)) edits += 1;
      if (event.failed) failed += 1;
    } else if (event.kind === "subagent") {
      subs += 1;
      if (event.failed) failed += 1;
    }
  }
  const parts = [
    commands ? text(`${commands} command${commands > 1 ? "s" : ""}`, `${commands} 条命令`) : "",
    edits ? text(`${edits} edit${edits > 1 ? "s" : ""}`, `${edits} 处修改`) : "",
    subs ? text(`${subs} sub-agent${subs > 1 ? "s" : ""}`, `${subs} 个子 Agent`) : "",
    failed ? text(`${failed} failed`, `${failed} 个失败`) : "",
  ].filter(Boolean);
  return parts.join(text(", ", "，"));
}

/** A message's last line: what it is doing and for how long while it goes, how long it took and what it did once it ended. */
function messageLine(run: ActivityRun, outcome: ReturnType<typeof runOutcome>, wait: { waitLabel: string } | null, options: ChartOptions): { line: string; strong: boolean } {
  const { text, now } = options;
  const started = run.started_at;
  const join = (...parts: string[]) => parts.filter(Boolean).join(" · ");
  const took = started !== null && run.ended_at !== null ? durationWords(run.ended_at - started, text) : "";
  const tagged = (word: string, strong = false) => ({ line: join(word, took), strong });
  switch (outcome) {
    case "waiting": {
      const since = momentOf(run);
      return { line: join(wait?.waitLabel || text("Waiting for you", "等你"), since !== null ? durationWords(Math.max(0, now - since), text) : ""), strong: true };
    }
    case "running": return { line: join(doingNow(run, options), run.execution_status !== "queued" && started !== null ? durationWords(Math.max(0, now - started), text) : ""), strong: true };
    case "paused": {
      const last = momentOf(run);
      return { line: join(text("Paused", "已暂停"), started !== null && last !== null ? durationWords(Math.max(0, last - started), text) : ""), strong: true };
    }
    case "failed": return tagged(text("Failed", "失败"), true);
    case "check-failed": return tagged(text("Check failed", "检查失败"), true);
    case "skipped": return tagged(text("Skipped: the previous run was still going", "已跳过：上一次运行还没结束"));
    case "missed": return tagged(text("Missed: OpenProgram was not running", "错过：当时 OpenProgram 没有运行"));
    case "cancelled": return tagged(text("Stopped by you", "你停下了"));
    case "interrupted": return tagged(text("Interrupted", "已中断"));
    case "nothing-new": return tagged(text("Nothing new", "无新内容"));
  }
  const done = workDone(run, text);
  return { line: join(took, done), strong: /\d+ (failed|个失败)/.test(done) };
}

const isLive = (outcome: ReturnType<typeof runOutcome>) => outcome === "running" || outcome === "waiting" || outcome === "paused";
/** Whether a run needs the reader's hand: an answer it waits for, or a pause to lift. */
const asksForHand = (outcome: ReturnType<typeof runOutcome>, wait: { answerLabel?: string } | null) => (outcome === "waiting" && Boolean(wait?.answerLabel)) || outcome === "paused";

const momentOf = (run: ActivityRun): number | null => {
  if (run.ended_at !== null) return run.ended_at;
  const last = listOf(run.steps).at(-1);
  return last?.at ?? run.started_at;
};

/** A message's title and the line under it. Its title is the task it
 *  worked on: one that has none yet has no title line, never a stand-in,
 *  and shows what was asked alone. A run nobody asked for is titled by
 *  what it said, once it said something. How it went is the last line. */
function messageWords(run: ActivityRun, task: string | null, options: ChartOptions): { title?: string; words?: string } {
  const { text } = options;
  const asked = run.asked_by === "you" && run.asked_preview.trim() ? readableRequest(firstLine(run.asked_preview), text) : "";
  const reply = run.reply.trim() ? firstLine(cleanRunTitle(run.reply, text)) : "";
  if (task) return { title: task, words: asked || reply || undefined };
  if (asked) return { words: asked };
  const named = run.reply.trim() || run.is_report || run.task_text?.trim();
  return named ? { title: runTitle(run, text, options.reportName(run)) } : {};
}

/** The task a run worked on last: its steps and sub-agents say which. */
export function runTask(run: ActivityRun): string | null {
  const events = listOf(run.steps);
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const task = taskOf(events[index]);
    if (task) return task;
  }
  return null;
}

function messageNode(run: ActivityRun, parent: ChartNode, options: ChartOptions, looks: SubLooks | null = null, task: string | null = null): ChartNode {
  const { now } = options;
  const wait = options.waitOf(run);
  const outcome = runOutcome(run, Boolean(wait));
  const moment = momentOf(run);
  const end = run.ended_at ?? now;
  const words = messageWords(run, task, options);
  // While it waits on the reader, what it asked takes the line under its title.
  if (outcome === "waiting" && wait?.question.trim()) words.words = `“${firstLine(wait.question)}”`;
  const node = makeNode({
    id: runRowId(run), depth: (parent?.depth ?? -1) + 1, parent, run, ...words, line: "", time: moment !== null ? clockTime(moment) : "",
    seconds: run.started_at !== null ? Math.max(0, end - run.started_at) : 0, hasActions: asksForHand(outcome, wait) && options.hasActions(run),
    outcome, live: isLive(outcome),
  });
  const steps = stepRows(run, node, end, options, looks);
  if (steps.length) node.kids = steps;
  Object.assign(node, messageLine(run, outcome, wait, options));
  return node;
}

function paintTree(node: ChartNode) {
  const shades = node.role <= 0 ? MESSAGE_SHADES : node.role === 1 ? STEP_SHADES : INSIDE_SHADES;
  const fade = node.role <= 0 ? 0.95 : 0.85;
  // Counted from the oldest, so a new message leaves the others' colours alone.
  node.kids?.forEach((kid, index, all) => {
    kid.paint = shade(node.paint, shades, all.length - 1 - index, fade);
    paintTree(kid);
  });
}

/** Everything under a block wears the colour of the agent that did it: a
 *  sub-agent's own from the row that started it, the main agent's elsewhere. */
function tintTree(node: ChartNode, color: string) {
  node.tint ??= color;
  node.kids?.forEach(kid => tintTree(kid, node.tint!));
}

/** Which steps fold: from the Steps column on, a block whose rows have nothing under them. */
function markFolds(node: ChartNode) {
  node.folds = node.depth >= 2 && Boolean(node.kids?.length) && node.kids!.every(kid => !kid.kids?.length);
  node.kids?.forEach(markFolds);
}

function topicLine(messages: ChartNode[], text: Translate): { line: string; strong: boolean } {
  const count = text(`${messages.length} message${messages.length > 1 ? "s" : ""}`, `${messages.length} 条消息`);
  const outcomes = new Set(messages.map(message => message.outcome));
  if (outcomes.has("waiting")) return { line: `${text("Waiting for you", "等你")} · ${count}`, strong: true };
  if (outcomes.has("running")) return { line: `${text("Running", "正在运行")} · ${count}`, strong: true };
  if (outcomes.has("paused")) return { line: `${text("Paused", "已暂停")} · ${count}`, strong: true };
  return { line: count, strong: false };
}

/** Topics reach the chart in time order: one that comes back is drawn again. */
function topicsOf(runs: ActivityRun[], text: Translate): Topic[] {
  const auto: Topic = { key: "auto", title: text("Automatic runs", "自动运行"), order: null };
  // A message the conversation has not put in a topic yet: its topic is room, not a name.
  const unsorted: Topic = { key: "unsorted", title: "", order: null, hidden: true };
  const topics: Topic[] = new Array(runs.length);
  let previous: Topic | null = null;
  for (let index = runs.length - 1; index >= 0; index -= 1) {
    const run = runs[index];
    if (run.asked_by !== "you") { topics[index] = auto; continue; }
    if (run.topic_id && run.topic_title) previous = { key: run.topic_id, title: run.topic_title, order: run.topic_order ?? 0 };
    topics[index] = previous ?? unsorted;
  }
  return topics;
}

const startOf = (run: ActivityRun) => run.started_at ?? run.ended_at ?? 0;
const dayKey = (seconds: number) => new Date(seconds * 1000).toDateString();

/** A date short enough for the time column: it sits left of the bars. */
function shortDay(seconds: number, text: Translate, now: number): string {
  const day = new Date(seconds * 1000);
  const month = text("short", "numeric") as "short" | "numeric";
  return day.getFullYear() === new Date(now * 1000).getFullYear()
    ? day.toLocaleDateString([], { month, day: "numeric" })
    : day.toLocaleDateString([], { year: "2-digit", month: "numeric", day: "numeric" });
}

function separator(newer: number, older: ActivityRun, text: Translate, now: number): { day: string | null; idle: string | null } | null {
  const olderAt = momentOf(older) ?? startOf(older);
  const seconds = newer - olderAt;
  const day = dayKey(newer) !== dayKey(olderAt) ? shortDay(olderAt, text, now) : null;
  const hours = Math.round(seconds / 3600);
  const idle = seconds < IDLE_SECONDS ? null : hours < 48 ? text(`${hours}h idle`, `空闲 ${hours} 小时`) : text(`${Math.round(hours / 24)}d idle`, `空闲 ${Math.round(hours / 24)} 天`);
  return day || idle ? { day, idle } : null;
}

/** The chart, newest first. */
export function buildChart(runs: ActivityRun[], options: ChartOptions): ChartRoot[] {
  const { text, now } = options;
  const topics = topicsOf(runs, text);
  const roots: ChartRoot[] = [];
  if (options.nextWake) roots.push({ kind: "next", id: "next-wake", time: clockTime(options.nextWake.at), title: text("Next wake", "下次唤醒"), line: options.nextWake.line, strong: Boolean(options.nextWake.strong) });
  if (runs[0] && dayKey(startOf(runs[0])) !== dayKey(now)) roots.push({ kind: "gap", id: "day-top", day: shortDay(startOf(runs[0]), text, now), idle: null });
  for (let start = 0; start < runs.length;) {
    let end = start;
    let label: ReturnType<typeof separator> = null;
    for (; end + 1 < runs.length; end += 1) {
      label = separator(startOf(runs[end]), runs[end + 1], text, now);
      if (label || topics[end + 1].key !== topics[start].key) break;
    }
    const topic = topics[start];
    const node = makeNode({ id: `topic:${runRowId(runs[end])}`, depth: 0, parent: null, title: topic.title, line: "", time: "", seconds: 0, hidden: topic.hidden });
    node.paint = { order: topic.order, hue: topic.order === null ? 0 : TOPIC_HUES[topic.order % TOPIC_HUES.length], lift: 0, chroma: 1 };
    node.kids = runs.slice(start, end + 1).map(run => messageNode(run, node, options));
    node.time = node.kids[0].time;
    Object.assign(node, topicLine(node.kids, text));
    paintTree(node);
    markFolds(node);
    roots.push({ kind: "topic", id: node.id, node });
    if (label && end + 1 < runs.length) roots.push({ kind: "gap", id: `gap:${runRowId(runs[end + 1])}`, ...label });
    start = end + 1;
  }
  if (options.earlier > 0) roots.push({ kind: "more", id: "more", label: text(`Show ${options.earlier} earlier`, `显示更早的 ${options.earlier} 个`) });
  sizeChart(roots);
  return roots;
}

/** One of the conversation's agents: the main agent on a branch, with the
 *  runs of that branch, newest first. */
export interface WorkAgent { key: string; name: string; color: string | null; current: boolean; runs: ActivityRun[] }
/** A task as the conversation last left it. */
export interface WorkTask { title: string; done: number; total: number }
export interface WorkOptions extends ChartOptions {
  tasks: ReadonlyMap<string, WorkTask>;
  /** Whether sub-agents this main agent started are still going and can be stopped. */
  stoppable?: (agentKey: string) => boolean;
}

interface WorkPart { agent: WorkAgent; run: ActivityRun; at: number }
interface WorkRow {
  key: string; title: string; order: number | null; newest: number; hidden?: boolean;
  parts: WorkPart[];
  /** The tasks its messages worked on. */
  tasks: Set<string>;
}

const taskOf = (event: ActivityRunEvent): string | null =>
  (event.kind === "step" || event.kind === "subagent") && event.task ? event.task : null;

/** How a sub-agent went, after its name on its row: how long it took, with
 *  a word only when it went wrong. One still going is told by its pulsing glyph. */
function subAgentLine(event: ActivitySubAgentEvent, text: Translate, now: number): { line: string; strong: boolean; live?: boolean } {
  const took = durationWords((event.ended_at ?? now) - event.at, text);
  if (event.never_started) return { line: text("Never started", "没有启动"), strong: true };
  if (event.failed) return { line: `${text("Failed", "失败")} · ${took}`, strong: true };
  if (event.ended_at === null) return { line: took, strong: true, live: true };
  return { line: took, strong: false };
}

/** FNV-1a, 32 bit. */
function hash(value: string): number {
  let h = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) { h ^= value.charCodeAt(index); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

/** Gives each sub-agent under one starter its colour, oldest first: the one
 *  its own id points at, or the next one that no main agent, its starter or
 *  an earlier sub-agent of the same starter wears (`used`, which the caller
 *  keeps across the starter's messages). Once every colour is worn the
 *  count starts again from `fixed`. A colour depends on nothing that comes
 *  later, so it stays as the conversation goes on. */
function colourSubAgents(events: ActivityRunEvent[], fixed: Set<string>, used: Set<string>, into: Map<ActivitySubAgentEvent, string>) {
  const subs = events.filter((event): event is ActivitySubAgentEvent => event.kind === "subagent").sort((a, b) => a.at - b.at);
  for (const event of subs) {
    if (SUB_COLORS.every(color => used.has(color))) { used.clear(); fixed.forEach(color => used.add(color)); }
    let pick = hash(event.job_id ?? `${event.name}:${event.at}`) % SUB_COLORS.length;
    for (let tries = 1; tries < SUB_COLORS.length && used.has(SUB_COLORS[pick]); tries += 1) pick = (pick + 1) % SUB_COLORS.length;
    const color = SUB_COLORS[pick];
    used.add(color);
    into.set(event, color);
    const inside = new Set([...fixed, color]);
    colourSubAgents([...event.children, ...listOf(event.steps)], inside, new Set(inside), into);
  }
}

/** The conversation's chart, newest first: a row for each topic, under it
 *  the messages of every branch's main agent in it, by time, each titled by
 *  the task it worked on, and each message's steps. From the messages on a
 *  bar's colour is the agent's: a main agent's branch colour, a sub-agent's
 *  own from the step that started it, with what it did one column on. Each
 *  agent's glyph is drawn once: a main agent's on its newest message, a
 *  sub-agent's on the step that started it. A run two branches share is the
 *  checked-out branch's. */
export function buildWorkChart(agents: WorkAgent[], options: WorkOptions): ChartRoot[] {
  const { text } = options;
  const rows = new Map<string, WorkRow>();
  const taken = new Set<string>();
  const ordered = [...agents].sort((a, b) => Number(b.current) - Number(a.current));
  const colorOf = (agent: WorkAgent) => agent.color ?? MAIN_COLOR;
  // A main agent is one starter over all its messages, shared ones too: a
  // sub-agent's colour is the same whichever branch is checked out.
  const mains = new Set(agents.map(colorOf));
  const subColors = new Map<ActivitySubAgentEvent, string>();
  for (const agent of agents) {
    const used = new Set(mains);
    [...agent.runs].sort((a, b) => startOf(a) - startOf(b)).forEach(run => colourSubAgents(listOf(run.steps), mains, used, subColors));
  }
  for (const agent of ordered) {
    const topics = topicsOf(agent.runs, text);
    agent.runs.forEach((run, index) => {
      const runId = runRowId(run);
      if (taken.has(runId)) return;
      taken.add(runId);
      const topic = topics[index];
      const at = momentOf(run) ?? startOf(run);
      let row = rows.get(topic.key);
      if (!row) rows.set(topic.key, row = { key: topic.key, title: topic.title, order: topic.order, hidden: topic.hidden, newest: at, parts: [], tasks: new Set() });
      row.newest = Math.max(row.newest, at);
      listOf(run.steps).forEach(event => { const task = taskOf(event); if (task) row!.tasks.add(task); });
      row.parts.push({ agent, run, at });
    });
  }
  const looks: SubLooks = { colorOf: event => subColors.get(event) ?? SUB_COLORS[0], now: options.now };
  const roots: ChartRoot[] = [];
  if (options.nextWake) roots.push({ kind: "next", id: "next-wake", time: clockTime(options.nextWake.at), title: text("Next wake", "下次唤醒"), line: options.nextWake.line, strong: Boolean(options.nextWake.strong) });
  // Each main agent's newest message, wherever it is.
  const newest = new Map<string, { node: ChartNode; at: number }>();
  for (const row of [...rows.values()].sort((a, b) => b.newest - a.newest)) {
    const node = makeNode({ id: `work:${row.key}`, depth: 0, parent: null, title: row.title, line: "", time: clockTime(row.newest), seconds: 0, hidden: row.hidden });
    node.paint = { order: row.order, hue: row.order === null ? 0 : TOPIC_HUES[row.order % TOPIC_HUES.length], lift: 0, chroma: 1 };
    // The agents' messages share the column, by time.
    node.kids = [...row.parts].sort((a, b) => b.at - a.at).map(part => {
      const task = runTask(part.run);
      const message = messageNode(part.run, node, options, looks, task ? options.tasks.get(task)?.title || task : null);
      tintTree(message, colorOf(part.agent));
      const seen = newest.get(part.agent.key);
      if (!seen || part.at > seen.at) newest.set(part.agent.key, { node: message, at: part.at });
      return message;
    });
    const { line, strong } = topicLine(node.kids, text);
    node.strong = strong;
    node.line = row.tasks.size && !strong ? `${line} · ${text(`${row.tasks.size} task${row.tasks.size === 1 ? "" : "s"}`, `${row.tasks.size} 个任务`)}` : line;
    markFolds(node);
    roots.push({ kind: "topic", id: node.id, node });
  }
  for (const agent of agents) {
    const hit = newest.get(agent.key)?.node;
    if (!hit) continue;
    hit.agent = { key: agent.key, name: agent.name, color: colorOf(agent), sub: false };
    if (options.stoppable?.(agent.key)) hit.stops = true;
  }
  sizeChart(roots);
  return roots;
}

/** How many columns the chart has: four, and one more each time a
 *  sub-agent started by a sub-agent did something of its own. */
export function levelsOf(roots: ChartRoot[]): number {
  let deepest = LEVELS - 1;
  for (const root of roots) if (root.kind === "topic") forEachNode(root.node, node => { if (node.depth > deepest) deepest = node.depth; });
  return deepest + 1;
}

/** Every block's height at every level. Run again after a block's `extra` changes. */
export function sizeChart(roots: ChartRoot[], levels: number = levelsOf(roots)) {
  for (const root of roots) if (root.kind === "topic") for (let level = 0; level < levels; level += 1) assign(root.node, natural(root.node, level), level);
}

/** Whether clicking the block opens details under it: a block with a next
 *  column goes there instead, and a call still running has nothing to add. */
export function opensInPlace(node: ChartNode): boolean {
  if (node.kids?.length) return false;
  const { event, run } = node;
  if (event) {
    if (event.kind === "attempt") return Boolean(event.failure_text || event.reason_code || event.outcome || event.checkpoint_id);
    if (event.kind === "subagent") return Boolean(event.brief.trim() || event.result?.trim() || event.error?.trim());
    return true;
  }
  return node.role === 1 && Boolean(run && (run.asked_preview.trim() || run.reply.trim()));
}

/** The room a closed block already gives what opens in it: a failed step's
 *  two lines of error. Its details grow from there, and shrink back to it. */
export const closedRoom = (node: ChartNode): number => node.role >= 2 && node.detail ? ERROR_HEIGHT : 0;

export const rootHeight = (root: Exclude<ChartRoot, { kind: "topic" }>): number => ROOT_HEIGHT[root.kind];

export function forEachNode(node: ChartNode, visit: (node: ChartNode) => void) {
  visit(node);
  node.kids?.forEach(kid => forEachNode(kid, visit));
}

/** A call's input for reading: the one thing it was given, or a line per
 *  field, instead of the JSON it travelled as. */
export function readableInput(input: unknown): string {
  if (typeof input === "string") return input.trim();
  if (input == null || typeof input !== "object") return input == null ? "" : String(input);
  const fields = Object.entries(input as Record<string, unknown>).filter(([, value]) => value != null && value !== "");
  const words = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value);
  if (fields.length === 1) return words(fields[0][1]).trim();
  return fields.map(([key, value]) => `${key}: ${words(value)}`).join("\n");
}

/** The most columns the chart shows side by side. */
export const MAX_COLUMNS = 3;
/** What the right sidebar takes around the chart: the card's gutter (16)
 *  and the Activity page's own padding (16). */
export const PANEL_INSET = 32;

/** The sidebar width at which exactly `count` columns fit at their natural
 *  width: the sidebar's snaps, the first of them its default. */
export const panelWidthFor = (count: number): number => count * COLUMN_WIDTH + PANEL_INSET;
export const PANEL_SNAP_WIDTHS: readonly number[] = Array.from({ length: MAX_COLUMNS }, (_, index) => panelWidthFor(index + 1));

/** How many whole columns a chart this wide shows: never a part of one.
 *  Between two snaps the columns stretch to fill the width. */
export function columnsFor(width: number, levels: number = LEVELS): number {
  return Math.max(1, Math.min(MAX_COLUMNS, levels, Math.floor((width + 1) / COLUMN_WIDTH)));
}

/** Which columns show: `focus` is the deepest column the reader went to, and
 *  the view ends on it, or starts at the first column when it fits there.
 *  `last` is also the level the chart is laid out at. */
export function chartWindow(focus: number, count: number, levels: number = LEVELS): { first: number; last: number } {
  const first = Math.max(0, Math.min(levels - count, focus - count + 1));
  return { first, last: Math.min(levels - 1, first + count - 1) };
}

/** The focus after the reader opens `column`: a column already in view moves
 *  nothing, and is remembered for when the sidebar narrows. */
export function focusOn(focus: number, count: number, column: number, levels: number = LEVELS): number {
  const { first, last } = chartWindow(focus, count, levels);
  if (column >= first && column <= last) return Math.max(focus, column);
  return Math.max(0, Math.min(levels - 1, column < first ? column + count - 1 : column));
}

/** One column further (1) or back (-1); null when the view is already at that end. */
export function stepFocus(focus: number, count: number, direction: number, levels: number = LEVELS): number | null {
  const { first, last } = chartWindow(focus, count, levels);
  if (direction > 0) return last >= levels - 1 ? null : last + 1;
  return first <= 0 ? null : last - 1;
}

/** How long a move between columns takes: the slow motion tier for a whole
 *  column, less for what is left of one, never under the fast tier. */
export const travelMs = (distance: number, column: number): number =>
  Math.round(Math.max(120, Math.min(240, 240 * Math.abs(distance) / Math.max(1, column))));
