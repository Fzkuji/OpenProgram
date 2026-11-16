// Task blocks: a reply groups a stretch of work under the task the agent
// declared for it (task_start / task_step). The backend keeps one segment per
// reply and task (openprogram/programs/tools/planning/todo/shared.py) and
// stamps every content block with its task and step; this module turns a
// reply's blocks and segments into the parts the bubble draws. Design:
// docs/reference/design/ui/chat/task-blocks.html.

import { create } from "zustand";
import type { AssistantBlock, ChatMsg } from "@/lib/session-store";
import { isAgentRunning, isOpen } from "./row-status.ts";

export type TaskStepStatus = "pending" | "in_progress" | "completed" | "dropped";

export interface TaskStep {
  id: string;
  subject: string;
  status: TaskStepStatus;
  /** The half sentence the agent left on a finished step. */
  result?: string;
}

export type SegmentState = "running" | "done" | "carried" | "displaced";

/** One task as one reply left it. */
export interface TaskSegment {
  /** The reply this segment belongs to. */
  anchorId: string;
  taskId: string;
  /** "" for the conversation's own agent, `agent:<id>` for a sub-agent. */
  scope: string;
  title: string;
  purpose: string;
  state: SegmentState;
  outcome: string;
  /** A task_step advanced it at least once. One that ended without ever
   *  advancing draws flat: no step of it was ticked. */
  advanced: boolean;
  /** The step in progress at the last change; kept after a stop. */
  current: string | null;
  /** Steps already finished when this segment began. */
  earlier: string[];
  startedAt: number;
  endedAt: number | null;
  updatedAt: number;
  steps: TaskStep[];
  /** A todo list stored before tasks existed: no title, no stamps. */
  legacy: boolean;
}

/** The model's own bookkeeping calls: never drawn as work. */
export const TASK_TOOL_NAMES: ReadonlySet<string> = new Set(["task_start", "task_step", "task_list"]);

export function isTaskTool(block: Pick<AssistantBlock, "type" | "tool">): boolean {
  return block.type === "tool" && TASK_TOOL_NAMES.has(block.tool || "");
}

function isLegacyTodoTool(block: Pick<AssistantBlock, "type" | "tool">): boolean {
  return block.type === "tool" && (block.tool || "").startsWith("todo_");
}

function normalizeStep(raw: unknown): TaskStep | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const id = typeof row.id === "string" ? row.id : String(row.id ?? "");
  const subject = typeof row.subject === "string" ? row.subject.trim() : "";
  if (!id || !subject) return null;
  const status: TaskStepStatus = row.status === "in_progress" || row.status === "completed" || row.status === "dropped"
    ? row.status : "pending";
  const result = typeof row.result === "string" ? row.result.trim() : "";
  return result ? { id, subject, status, result } : { id, subject, status };
}

const num = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
const str = (value: unknown): string => (typeof value === "string" ? value : "");

function normalizeSegment(raw: unknown): TaskSegment | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const anchorId = str(row.anchor_id);
  const steps = Array.isArray(row.todos)
    ? row.todos.map(normalizeStep).filter((step): step is TaskStep => step !== null)
    : [];
  const legacy = !("title" in row);
  const taskId = row.plan_id != null && row.plan_id !== "" ? String(row.plan_id) : steps[0]?.id ?? "";
  if (!anchorId || !taskId || (legacy && steps.length === 0)) return null;
  const state: SegmentState = row.state === "done" || row.state === "carried" || row.state === "displaced"
    ? row.state : "running";
  return {
    anchorId,
    taskId,
    scope: str(row.scope),
    title: str(row.title).trim(),
    purpose: str(row.purpose).trim(),
    state,
    outcome: str(row.outcome).trim(),
    advanced: row.advanced === true,
    current: row.current != null && row.current !== "" ? String(row.current) : null,
    earlier: Array.isArray(row.earlier) ? row.earlier.map(String) : [],
    startedAt: num(row.started_at) || num(row.created_at),
    endedAt: num(row.ended_at) || null,
    updatedAt: num(row.updated_at),
    steps,
    legacy,
  };
}

export function normalizeSegments(raw: unknown): TaskSegment[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(normalizeSegment).filter((segment): segment is TaskSegment => segment !== null);
}

/** A reply a worker restart cut off is drawn by the reply that carries it
 *  on, so its segments go there too. */
export function placedAnchor(anchorId: string, rowsById: Readonly<Record<string, ChatMsg | undefined>>): string {
  let id = anchorId;
  const seen = new Set<string>();
  for (;;) {
    const next = rowsById[id]?.continuedBy;
    if (!next || seen.has(next) || !rowsById[next]) return id;
    seen.add(id);
    id = next;
  }
}

function idOrder(id: string): number {
  const n = Number(id);
  return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
}

/** Stored todo lists only: one list per agent out of the lists placed under
 *  one reply. Several of the same agent become one, every todo once, as the
 *  list changed last left it, in creation order. */
export function oneListPerAgent(lists: readonly TaskSegment[]): TaskSegment[] {
  const byScope = new Map<string, TaskSegment[]>();
  for (const list of lists) {
    const held = byScope.get(list.scope);
    if (held) held.push(list);
    else byScope.set(list.scope, [list]);
  }
  return [...byScope.values()].map((held) => {
    if (held.length === 1) return held[0];
    const steps = new Map<string, TaskStep>();
    const seen = new Map<string, number>();
    for (const list of [...held].sort((a, b) => a.updatedAt - b.updatedAt)) {
      for (const step of list.steps) {
        if (!seen.has(step.id)) seen.set(step.id, seen.size);
        steps.set(step.id, step);
      }
    }
    return {
      ...held[0],
      updatedAt: Math.max(...held.map((list) => list.updatedAt)),
      steps: [...steps.values()].sort((a, b) =>
        idOrder(a.id) - idOrder(b.id) || (seen.get(a.id) ?? 0) - (seen.get(b.id) ?? 0)),
    };
  });
}

/** What one reply draws. */
export interface RowTasks {
  /** Tasks with a segment under this reply, one per task. */
  segments: TaskSegment[];
  /** Stored todo lists that first appear under this reply, at their latest. */
  legacy: TaskSegment[];
  /** By task: the loaded reply holding the same task's previous segment. */
  earlierAnchor: Record<string, string>;
}

export const NO_ROW_TASKS: RowTasks = { segments: [], legacy: [], earlierAnchor: {} };

/** Two segments of one task under one drawn reply (a restart cut the reply
 *  in two): the newer snapshot, from the earlier start. */
function mergeSegments(a: TaskSegment, b: TaskSegment): TaskSegment {
  const [older, newer] = a.updatedAt <= b.updatedAt ? [a, b] : [b, a];
  const first = (older.startedAt || Infinity) <= (newer.startedAt || Infinity) ? older : newer;
  return { ...newer, startedAt: first.startedAt, earlier: first.earlier };
}

/** Every reply's tasks. Segments whose reply is not in `rowIds` (another
 *  branch, a sub-agent's own conversation, a page not loaded) are left out. */
export function tasksByRow(
  segments: readonly TaskSegment[],
  rowIds: readonly string[],
  rowsById: Readonly<Record<string, ChatMsg | undefined>>,
): Map<string, RowTasks> {
  const index = new Map(rowIds.map((id, at) => [id, at]));
  const out = new Map<string, RowTasks>();
  const entry = (row: string): RowTasks => {
    let held = out.get(row);
    if (!held) out.set(row, held = { segments: [], legacy: [], earlierAnchor: {} });
    return held;
  };
  const current = new Map<string, TaskSegment>();
  const stored = new Map<string, TaskSegment[]>();
  for (const segment of segments) {
    const row = placedAnchor(segment.anchorId, rowsById);
    if (!index.has(row)) continue;
    const placed = row === segment.anchorId ? segment : { ...segment, anchorId: row };
    if (segment.legacy) {
      const list = stored.get(row);
      if (list) list.push(placed);
      else stored.set(row, [placed]);
      continue;
    }
    const key = `${row}\u0000${segment.taskId}`;
    const held = current.get(key);
    current.set(key, held ? mergeSegments(held, placed) : placed);
  }
  const byTask = new Map<string, TaskSegment[]>();
  for (const segment of current.values()) {
    entry(segment.anchorId).segments.push(segment);
    const key = `${segment.scope}\u0000${segment.taskId}`;
    const list = byTask.get(key);
    if (list) list.push(segment);
    else byTask.set(key, [segment]);
  }
  for (const list of byTask.values()) {
    list.sort((a, b) => index.get(a.anchorId)! - index.get(b.anchorId)!);
    for (let at = 1; at < list.length; at++) entry(list[at].anchorId).earlierAnchor[list[at].taskId] = list[at - 1].anchorId;
  }
  // A stored list is the same list wherever it shares a todo: it is drawn
  // once, under the first loaded reply that has it, as it was last left.
  const groups: { row: string; lists: TaskSegment[] }[] = [];
  const groupOf = new Map<string, number>();
  for (const row of [...stored.keys()].sort((a, b) => index.get(a)! - index.get(b)!)) {
    for (const list of oneListPerAgent(stored.get(row)!)) {
      const keys = list.steps.map((step) => `${list.scope}\u0000${step.id}`);
      let at = keys.map((key) => groupOf.get(key)).find((found) => found !== undefined);
      if (at === undefined) at = groups.push({ row, lists: [] }) - 1;
      groups[at].lists.push(list);
      for (const key of keys) groupOf.set(key, at);
    }
  }
  for (const group of groups) {
    const [merged] = oneListPerAgent(group.lists);
    entry(group.row).legacy.push({ ...merged, anchorId: group.row, taskId: group.lists[0].taskId });
  }
  return out;
}

const placements = new WeakMap<readonly TaskSegment[], { order: readonly string[]; byRow: Map<string, RowTasks> }>();

/** One reply's tasks, computed once per (segments, row order). */
export function rowTasks(
  segments: readonly TaskSegment[],
  rowIds: readonly string[],
  rowsById: Readonly<Record<string, ChatMsg | undefined>>,
  rowId: string,
): RowTasks {
  if (!segments.length) return NO_ROW_TASKS;
  let held = placements.get(segments);
  if (!held || held.order !== rowIds) {
    held = { order: rowIds, byRow: tasksByRow(segments, rowIds, rowsById) };
    placements.set(segments, held);
  }
  return held.byRow.get(rowId) ?? NO_ROW_TASKS;
}

/** How a task block is drawn. A stop, a failure and an interruption are not
 *  stored: the segment still says running and the reply's status tells. */
export type DrawnState = "running" | "done" | "carried" | "displaced" | "stopped" | "failed" | "interrupted";

/** `settled`: the turn's execution is already terminal although the reply's
 *  status has not caught up. A reply that waits on the person (paused) has
 *  not ended: its block is still running, drawn without motion. */
export function drawnState(segment: TaskSegment, rowStatus: string | null | undefined, settled = false): DrawnState {
  if (segment.legacy) return segment.steps.every((step) => step.status === "completed" || step.status === "dropped") ? "done" : "carried";
  if (segment.state !== "running") return segment.state;
  if (rowStatus === "cancelled") return "stopped";
  if (rowStatus === "error") return "failed";
  if (rowStatus === "interrupted") return "interrupted";
  return isOpen(rowStatus) && !settled ? "running" : "carried";
}

/** Sub-agents the reply started that have not finished. */
export function runningAgentCount(msg: ChatMsg | undefined): number {
  return (msg?.attachCards ?? []).filter((card) => isAgentRunning(card.attach?.status)).length;
}

/** A task that ended without one advance: the runtime closed it and ticked
 *  nothing, so its steps are an outline and it shows no progress. */
export function neverAdvanced(segment: TaskSegment): boolean {
  return !segment.legacy && !segment.advanced && segment.state === "done";
}

/** Finished and counted steps; dropped steps leave the total. `on` is the
 *  place of the step in progress among the counted ones (what a running
 *  block shows), the finished count when none is. Null when the task has no
 *  steps or ended without advancing: no progress is shown then. */
export function progressOf(segment: TaskSegment): { done: number; total: number; on: number } | null {
  const counted = segment.steps.filter((step) => step.status !== "dropped");
  if (!counted.length || neverAdvanced(segment)) return null;
  const done = counted.filter((step) => step.status === "completed").length;
  const working = counted.findIndex((step) => step.status === "in_progress");
  return { done, total: counted.length, on: working >= 0 ? working + 1 : done };
}

/** Whether a block is open before the reader chooses. */
export function autoOpen(drawn: DrawnState, lastTaskInReply: boolean, agentsRunning: boolean, legacy = false): boolean {
  if (legacy) return false;
  if (agentsRunning) return true;
  if (drawn === "stopped" || drawn === "failed" || drawn === "interrupted") return true;
  return drawn === "running" && lastTaskInReply;
}

/** The step a block is on: the one being worked on, or the one a stop left
 *  it on. It is named in the folded header and marked on its node; its work
 *  stays folded until the reader opens it. */
export function currentStepId(segment: TaskSegment, drawn: DrawnState): string | null {
  if (drawn !== "running" && drawn !== "stopped" && drawn !== "failed" && drawn !== "interrupted") return null;
  const working = segment.steps.find((step) => step.status === "in_progress")?.id ?? null;
  return drawn === "running" ? working ?? segment.current : segment.current ?? working;
}

export interface WorkItem { b: AssistantBlock; i: number }

export interface TaskPart {
  kind: "task";
  key: string;
  segment: TaskSegment;
  /** Everything done for the task in this reply, in order. */
  items: WorkItem[];
  /** No per-step work: the outline, then the work in one run (a task with
   *  no steps, or one that ended without advancing). */
  flat: boolean;
  /** The outline: steps not finished before this segment, with their work. */
  steps: { step: TaskStep; items: WorkItem[] }[];
  /** Steps finished in earlier replies, drawn as one row. */
  earlier: number;
}

export type ReplyPart =
  | { kind: "text"; b: AssistantBlock; i: number }
  | { kind: "steering"; b: AssistantBlock; i: number }
  | { kind: "exec"; key: string; items: WorkItem[] }
  | TaskPart;

function taskPart(segment: TaskSegment): TaskPart {
  const earlier = new Set(segment.earlier);
  const listed = segment.legacy ? segment.steps : segment.steps.filter((step) => !earlier.has(step.id));
  return {
    kind: "task",
    key: `task:${segment.taskId}`,
    segment,
    items: [],
    flat: !segment.legacy && (listed.length === 0 || neverAdvanced(segment)),
    steps: listed.map((step) => ({ step, items: [] })),
    earlier: segment.steps.length - listed.length,
  };
}

function placeUnderSteps(part: TaskPart): void {
  if (part.flat || !part.steps.length) return;
  const byId = new Map(part.steps.map((row) => [row.step.id, row]));
  let at = part.steps[0];
  for (const item of part.items) {
    at = (item.b.step ? byId.get(item.b.step) : undefined) ?? at;
    at.items.push(item);
  }
}

/** A reply's blocks as the parts it draws, in order.
 *
 *  - A block belongs to the task its stamp names, when that task's segment
 *    is here; a stamp whose segment has not arrived counts as none.
 *  - A task's block runs from its first stamped block to its last stamped
 *    piece of work: a real tool call, and, while the segment is not closed
 *    (the task is being worked on, or was stopped there), thinking after
 *    that call too, so a step's work is drawn inside it from the start.
 *    Stamped text after that is outside: the text between two tasks and
 *    the final answer stay visible.
 *  - The task tools themselves are never drawn.
 *  - A stored todo list draws as one block at the reply's first todo call,
 *    and that reply's todo calls are not drawn; without such a call it
 *    draws nothing and the calls stay in their summary row.
 *  - A reply with no segment and no stamp gives the text and execution
 *    parts it always gave. */
export function layoutReply(
  blocks: readonly AssistantBlock[],
  blockKeys: readonly string[],
  tasks: Pick<RowTasks, "segments" | "legacy"> = NO_ROW_TASKS,
): ReplyPart[] {
  const segments = new Map(tasks.segments.map((segment) => [segment.taskId, segment]));
  const firstTodo = tasks.legacy.length ? blocks.findIndex(isLegacyTodoTool) : -1;
  const first = new Map<string, number>();
  const last = new Map<string, number>();
  blocks.forEach((block, at) => {
    const task = block.task;
    if (!task || !segments.has(task)) return;
    if (!first.has(task)) first.set(task, at);
    const work = block.type === "tool"
      ? !isTaskTool(block)
      : (block.type === "thinking" || block.type === "restart") && segments.get(task)!.state === "running";
    if (work) last.set(task, at);
  });
  const owner: (string | null)[] = blocks.map(() => null);
  for (const [task, from] of [...first].sort((a, b) => a[1] - b[1])) {
    const to = last.get(task);
    if (to === undefined) continue;
    for (let at = from; at <= to; at++) owner[at] ??= task;
  }
  const parts: ReplyPart[] = [];
  const placed = new Map<string, TaskPart>();
  const place = (segment: TaskSegment) => {
    const part = taskPart(segment);
    placed.set(segment.taskId, part);
    parts.push(part);
  };
  blocks.forEach((b, i) => {
    if (i === firstTodo) for (const list of tasks.legacy) parts.push(taskPart(list));
    for (const [task, from] of first) if (from === i) place(segments.get(task)!);
    if (isTaskTool(b) || (firstTodo >= 0 && isLegacyTodoTool(b))) return;
    const task = owner[i];
    if (task) {
      placed.get(task)!.items.push({ b, i });
      return;
    }
    if (b.type === "text" || b.type === "steering") {
      parts.push({ kind: b.type, b, i });
      return;
    }
    const open = parts[parts.length - 1];
    if (open?.kind === "exec") open.items.push({ b, i });
    else parts.push({ kind: "exec", key: `exec:${blockKeys[i]}`, items: [{ b, i }] });
  });
  // A segment that arrived before any of its blocks: the newest thing in the reply.
  for (const segment of tasks.segments) if (!placed.has(segment.taskId)) place(segment);
  for (const part of placed.values()) placeUnderSteps(part);
  return parts;
}

/** The part a retry or a stop tag rides: the reply's last task block. */
export function lastTaskPart(parts: readonly ReplyPart[]): TaskPart | undefined {
  for (let at = parts.length - 1; at >= 0; at--) {
    const part = parts[at];
    if (part.kind === "task" && !part.segment.legacy) return part;
  }
  return undefined;
}

/** What the message rail lists for a task under one reply: its step count,
 *  or the steps this reply covered when the task spans several. */
export function railRange(segment: TaskSegment): { steps?: number; from?: number; to?: number } {
  const counted = segment.steps.filter((step) => step.status !== "dropped");
  const before = counted.filter((step) => segment.earlier.includes(step.id)).length;
  if (!before && segment.state !== "carried") return { steps: counted.length };
  const done = counted.filter((step) => step.status === "completed").length;
  const to = segment.state === "running" || segment.state === "done" ? counted.length : done;
  return { from: before + 1, to: Math.max(to, before + 1) };
}

interface TaskState {
  segmentsBySession: Record<string, TaskSegment[]>;
  /** A session load: every segment of the conversation. */
  setAll(sessionId: string, raw: unknown): void;
  /** `todos_update.changed`: each replaces the segment of its reply and task. */
  upsert(sessionId: string, raw: unknown): void;
}

export const useTaskStore = create<TaskState>((set) => ({
  segmentsBySession: {},
  setAll: (sessionId, raw) => set((state) => ({
    segmentsBySession: { ...state.segmentsBySession, [sessionId]: normalizeSegments(raw) },
  })),
  upsert: (sessionId, raw) => set((state) => {
    const changed = normalizeSegments(raw);
    if (!changed.length) return state;
    const list = [...(state.segmentsBySession[sessionId] ?? [])];
    for (const segment of changed) {
      const at = list.findIndex((held) => held.anchorId === segment.anchorId && held.taskId === segment.taskId);
      if (at >= 0) list[at] = segment;
      else list.push(segment);
    }
    return { segmentsBySession: { ...state.segmentsBySession, [sessionId]: list } };
  }),
}));
