/** What an Agent's time chart draws: topics, the messages in them, each
 *  message's steps and what is inside a step, with every block's height at
 *  each level of detail. Pure, so the node tests can load it. */
import type { ActivityRun, ActivityRunEvent, ActivitySubAgentEvent } from "../../../lib/net/execution/activity-runs-client";
import type { Translate } from "./activity-page-types";
import { attemptLabel, cleanRunTitle, clockTime, durationWords, eventLabel, firstLine, runOutcome, runRowId, runTitle, stepTitle } from "./activity-run-wording.ts";
import { readableRequest } from "../debugger/debugger-presentation.ts";

export const COLUMN_WIDTH = 256;
export const HEADER_HEIGHT = 30;
export const LEVELS = 4;
const GROUP_CAP = 120;
const ROOT_HEIGHT = { gap: 32, next: 60, more: 36 } as const;
const IDLE_SECONDS = 6 * 3600;
const MAX_SECONDS = 300;
const ACTIONS_HEIGHT = 28;
const ERROR_HEIGHT = 38;

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

export interface ChartNode {
  id: string;
  depth: number;
  time: string;
  /** Topics and messages have a title; a step is its line alone. */
  title?: string;
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
  | { kind: "next"; id: string; time: string; title: string; line: string }
  | { kind: "more"; id: string; label: string };

export interface ChartOptions {
  text: Translate;
  now: number;
  waitOf: (run: ActivityRun) => { waitLabel: string; question: string } | null;
  hasActions: (run: ActivityRun) => boolean;
  /** Calls under way that have no saved step yet. */
  liveCalls: (run: ActivityRun) => { id: string; at: number; label: string }[];
  reportName: (run: ActivityRun) => string | null;
  nextWake: { at: number; line: string } | null;
  earlier: number;
}

interface Topic { key: string; title: string; order: number | null }

const makeNode = (values: Pick<ChartNode, "id" | "depth" | "time" | "line" | "seconds" | "parent"> & Partial<ChartNode>): ChartNode => ({
  strong: false, paint: { order: null, hue: 0, lift: 0, chroma: 1 }, hasActions: false, extra: 0, heights: [], top: 0, height: 0, ...values,
});

const rowHeight = (node: ChartNode): number => {
  if (node.depth === 0) return 60;
  const seconds = Math.min(node.seconds, MAX_SECONDS);
  if (node.depth === 1) return Math.round(60 + Math.sqrt(seconds / MAX_SECONDS) * 26) + (node.hasActions ? ACTIONS_HEIGHT : 0) + node.extra;
  // Opened details say the error in full, so they stand in for its two lines.
  return Math.round(34 + Math.sqrt(Math.max(1, seconds)) * 0.8) + (node.extra || (node.detail ? ERROR_HEIGHT : 0));
};

function natural(node: ChartNode, level: number): number {
  if (node.depth >= level || !node.kids) return rowHeight(node);
  const sum = node.kids.reduce((total, kid) => total + natural(kid, level), 0);
  if (node.depth !== 2) return Math.max(rowHeight(node), sum);
  // A group makes room for the details opened inside it.
  const opened = node.kids.reduce((total, kid) => total + kid.extra, 0);
  return Math.max(rowHeight(node), Math.min(GROUP_CAP + opened, sum));
}

function assign(node: ChartNode, height: number, level: number) {
  node.heights[level] = height;
  if (!node.kids) return;
  // The last column scrolls inside its own cell, so its rows keep their size.
  if (node.depth === 2 && level === LEVELS - 1) { node.kids.forEach(kid => assign(kid, natural(kid, level), level)); return; }
  const basis = node.kids.map(kid => natural(kid, node.depth < level ? level : node.depth + 1));
  const sum = basis.reduce((total, value) => total + value, 0);
  node.kids.forEach((kid, index) => assign(kid, node.depth < level ? basis[index] + (index === 0 ? height - sum : 0) : height * basis[index] / sum, level));
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

function eventRow(item: Item, end: number, depth: number, parent: ChartNode, text: Translate): ChartNode {
  const { id, at, event } = item;
  const base = { id, depth, parent, time: clockTime(at, true), seconds: Math.max(0, end - at) };
  if (!event) return makeNode({ ...base, line: `${text("Running", "正在运行")} ${item.live}`, strong: true });
  const { label } = eventLabel(event, text);
  if (event.kind === "step") return makeNode({ ...base, event, line: label, strong: event.failed, detail: event.failed ? errorOf(event.result) : undefined });
  if (event.kind === "attempt") return makeNode({ ...base, event, line: label, strong: true, detail: errorOf(event.failure_text ?? event.reason_code) });
  if (event.kind === "subagent") return subAgentRow(event, id, end, depth, parent, text);
  return makeNode({ ...base, event, line: label });
}

function subAgentRow(event: ActivitySubAgentEvent, id: string, end: number, depth: number, parent: ChartNode, text: Translate): ChartNode {
  const finished = event.ended_at ?? end;
  const node = makeNode({
    id, depth, parent, event, time: clockTime(event.ended_at ?? event.at, true), seconds: Math.max(0, finished - event.at),
    line: `${event.failed ? `${text("Failed", "失败")} · ` : ""}${text("Sub-agent", "子 Agent")} · ${event.name}`,
    strong: event.failed, detail: event.failed ? errorOf(event.error) : undefined,
  });
  const inner: Item[] = [
    ...listOf(event.steps).map((step, index) => ({ id: `${id}:${index}`, at: step.at, event: step })),
    ...event.children.map((child, index) => ({ id: `${id}:agent:${index}`, at: child.at, event: child })),
  ].sort((a, b) => a.at - b.at);
  if (depth >= LEVELS - 1 || inner.length === 0) return node;
  node.kids = inner.map((item, index) => eventRow(item, inner[index + 1]?.at ?? finished, depth + 1, node, text)).reverse();
  return node;
}

/** Calls of one function in a row share a line; each call is in the next column. */
function stepRows(run: ActivityRun, parent: ChartNode, end: number, options: ChartOptions): ChartNode[] {
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
      const group = makeNode({ id: `${items[position].id}:group`, depth: 2, parent, time: clockTime(items[last].at, true), seconds: Math.max(0, endOf(last) - items[position].at), line: groupLine(first.name, first.input, last - position + 1, text) });
      group.kids = items.slice(position, last + 1).map((item, index) => eventRow(item, endOf(position + index), 3, group, text)).reverse();
      rows.push(group);
    } else {
      rows.push(eventRow(items[position], endOf(position), 2, parent, text));
    }
    position = last + 1;
  }
  return rows.reverse();
}

function messageLine(run: ActivityRun, outcome: ReturnType<typeof runOutcome>, wait: { waitLabel: string; question: string } | null, steps: ChartNode[], options: ChartOptions): { line: string; strong: boolean } {
  const { text, now } = options;
  const started = run.started_at;
  const took = started !== null && run.ended_at !== null ? durationWords(run.ended_at - started, text) : "";
  const tagged = (word: string, strong = false) => ({ line: [word, took].filter(Boolean).join(" · "), strong });
  switch (outcome) {
    case "waiting": return { line: `${text("Waiting", "等待")} · ${wait?.question || wait?.waitLabel || ""}`, strong: true };
    case "running": {
      const doing = steps[0]?.line.startsWith(text("Running", "正在运行")) ? steps[0].line : steps[0] ? `${text("Running", "正在运行")} ${steps[0].line}` : text("Running", "正在运行");
      return { line: [doing, started !== null ? durationWords(now - started, text) : ""].filter(Boolean).join(" · "), strong: true };
    }
    case "failed": return tagged(text("Failed", "失败"), true);
    case "check-failed": return tagged(text("Check failed", "检查失败"), true);
    case "skipped": return tagged(text("Skipped: the previous run was still going", "已跳过：上一次运行还没结束"));
    case "missed": return tagged(text("Missed: OpenProgram was not running", "错过：当时 OpenProgram 没有运行"));
    case "cancelled": return tagged(text("Cancelled", "已取消"));
    case "interrupted": return tagged(text("Interrupted", "已中断"));
    case "paused": return tagged(text("Paused", "已暂停"));
    case "nothing-new": return tagged(text("Nothing new", "无新内容"));
  }
  const failedSteps = listOf(run.steps).filter(event => event.kind === "step" && event.failed).length;
  const note = failedSteps ? text(`${failedSteps} step${failedSteps > 1 ? "s" : ""} failed`, `${failedSteps} 步失败`) : run.reply.trim() ? firstLine(cleanRunTitle(run.reply, text)) : "";
  return { line: [took, note].filter(Boolean).join(" · "), strong: false };
}

const isLive = (outcome: ReturnType<typeof runOutcome>) => outcome === "running" || outcome === "waiting" || outcome === "paused";

const momentOf = (run: ActivityRun): number | null => {
  if (run.ended_at !== null) return run.ended_at;
  const last = listOf(run.steps).at(-1);
  return last?.at ?? run.started_at;
};

function messageNode(run: ActivityRun, parent: ChartNode, options: ChartOptions): ChartNode {
  const { text, now } = options;
  const wait = options.waitOf(run);
  const outcome = runOutcome(run, Boolean(wait));
  const moment = momentOf(run);
  const end = run.ended_at ?? now;
  const title = run.asked_by === "you" && run.asked_preview.trim() ? readableRequest(firstLine(run.asked_preview), text) : runTitle(run, text, options.reportName(run));
  const node = makeNode({
    id: runRowId(run), depth: 1, parent, run, title, line: "", time: moment !== null ? clockTime(moment) : "",
    seconds: run.started_at !== null ? Math.max(0, end - run.started_at) : 0, hasActions: isLive(outcome) && options.hasActions(run),
  });
  const steps = stepRows(run, node, end, options);
  if (steps.length) node.kids = steps;
  Object.assign(node, messageLine(run, outcome, wait, steps, options));
  return node;
}

function paintTree(node: ChartNode) {
  const shades = node.depth === 0 ? MESSAGE_SHADES : node.depth === 1 ? STEP_SHADES : INSIDE_SHADES;
  const fade = node.depth === 0 ? 0.95 : 0.85;
  // Counted from the oldest, so a new message leaves the others' colours alone.
  node.kids?.forEach((kid, index, all) => {
    kid.paint = shade(node.paint, shades, all.length - 1 - index, fade);
    paintTree(kid);
  });
}

function topicLine(messages: ChartNode[], text: Translate): { line: string; strong: boolean } {
  const count = text(`${messages.length} message${messages.length > 1 ? "s" : ""}`, `${messages.length} 条消息`);
  const outcomes = messages.map(message => message.line);
  if (outcomes.some(line => line.startsWith(text("Waiting", "等待")))) return { line: text("Waiting for your answer", "等你回答"), strong: true };
  if (outcomes.some(line => line.startsWith(text("Running", "正在运行")))) return { line: `${text("Running", "正在运行")} · ${count}`, strong: true };
  return { line: count, strong: false };
}

/** Topics reach the chart in time order: one that comes back is drawn again. */
function topicsOf(runs: ActivityRun[], text: Translate): Topic[] {
  const auto: Topic = { key: "auto", title: text("Automatic runs", "自动运行"), order: null };
  const unsorted: Topic = { key: "unsorted", title: text("Not sorted yet", "还没分类"), order: null };
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
  if (options.nextWake) roots.push({ kind: "next", id: "next-wake", time: clockTime(options.nextWake.at), title: text("Next wake", "下次唤醒"), line: options.nextWake.line });
  if (runs[0] && dayKey(startOf(runs[0])) !== dayKey(now)) roots.push({ kind: "gap", id: "day-top", day: shortDay(startOf(runs[0]), text, now), idle: null });
  for (let start = 0; start < runs.length;) {
    let end = start;
    let label: ReturnType<typeof separator> = null;
    for (; end + 1 < runs.length; end += 1) {
      label = separator(startOf(runs[end]), runs[end + 1], text, now);
      if (label || topics[end + 1].key !== topics[start].key) break;
    }
    const topic = topics[start];
    const node = makeNode({ id: `topic:${runRowId(runs[end])}`, depth: 0, parent: null, title: topic.title, line: "", time: "", seconds: 0 });
    node.paint = { order: topic.order, hue: topic.order === null ? 0 : TOPIC_HUES[topic.order % TOPIC_HUES.length], lift: 0, chroma: 1 };
    node.kids = runs.slice(start, end + 1).map(run => messageNode(run, node, options));
    node.time = node.kids[0].time;
    Object.assign(node, topicLine(node.kids, text));
    paintTree(node);
    roots.push({ kind: "topic", id: node.id, node });
    if (label && end + 1 < runs.length) roots.push({ kind: "gap", id: `gap:${runRowId(runs[end + 1])}`, ...label });
    start = end + 1;
  }
  if (options.earlier > 0) roots.push({ kind: "more", id: "more", label: text(`Show ${options.earlier} earlier`, `显示更早的 ${options.earlier} 个`) });
  sizeChart(roots);
  return roots;
}

/** Every block's height at every level. Run again after a block's `extra` changes. */
export function sizeChart(roots: ChartRoot[]) {
  for (const root of roots) if (root.kind === "topic") for (let level = 0; level < LEVELS; level += 1) assign(root.node, natural(root.node, level), level);
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
  return node.depth === 1 && Boolean(run && (run.asked_preview.trim() || run.reply.trim()));
}

/** The room a closed block already gives what opens in it: a failed step's
 *  two lines of error. Its details grow from there, and shrink back to it. */
export const closedRoom = (node: ChartNode): number => node.depth >= 2 && node.detail ? ERROR_HEIGHT : 0;

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
export function columnsFor(width: number): number {
  return Math.max(1, Math.min(MAX_COLUMNS, LEVELS, Math.floor((width + 1) / COLUMN_WIDTH)));
}

/** Which columns show: `focus` is the deepest column the reader went to, and
 *  the view ends on it, or starts at the first column when it fits there.
 *  `last` is also the level the chart is laid out at. */
export function chartWindow(focus: number, count: number): { first: number; last: number } {
  const first = Math.max(0, Math.min(LEVELS - count, focus - count + 1));
  return { first, last: Math.min(LEVELS - 1, first + count - 1) };
}

/** The focus after the reader opens `column`: a column already in view moves
 *  nothing, and is remembered for when the sidebar narrows. */
export function focusOn(focus: number, count: number, column: number): number {
  const { first, last } = chartWindow(focus, count);
  if (column >= first && column <= last) return Math.max(focus, column);
  return Math.max(0, Math.min(LEVELS - 1, column < first ? column + count - 1 : column));
}

/** One column further (1) or back (-1); null when the view is already at that end. */
export function stepFocus(focus: number, count: number, direction: number): number | null {
  const { first, last } = chartWindow(focus, count);
  if (direction > 0) return last >= LEVELS - 1 ? null : last + 1;
  return first <= 0 ? null : last - 1;
}

/** How long a move between columns takes: the slow motion tier for a whole
 *  column, less for what is left of one, never under the fast tier. */
export const travelMs = (distance: number, column: number): number =>
  Math.round(Math.max(120, Math.min(240, 240 * Math.abs(distance) / Math.max(1, column))));
