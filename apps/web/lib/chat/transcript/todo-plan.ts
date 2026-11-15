// The agent's task list in the transcript. The backend records, for each
// turn that changed the session's todo board, the plan as that turn left it
// (openprogram/programs/tools/planning/todo/shared.py place_plan). Each of
// those placed plans is drawn under the reply of its turn while the turn
// runs; see planShown for what stays afterwards.

import { create } from "zustand";
import type { ChatMsg } from "@/lib/session-store";
import { isAgentRunning, isLive, isStopped } from "./row-status.ts";

export type TodoStatus = "pending" | "in_progress" | "completed";

export interface TodoItem {
  id: string;
  subject: string;
  status: TodoStatus;
}

/** One plan as one turn left it. */
export interface PlacedPlan {
  /** The assistant reply of the turn that last changed this plan. */
  anchorId: string;
  /** The plan's first todo id: the same plan across turns. */
  planId: string;
  /** "" for the conversation's own agent, `agent:<id>` for a sub-agent. */
  scope: string;
  updatedAt: number;
  todos: TodoItem[];
  /** Carried under a later running turn that has no plan of its own. */
  stale?: boolean;
}

function normalizeTodo(raw: unknown): TodoItem | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const id = typeof row.id === "string" ? row.id : String(row.id ?? "");
  const subject = typeof row.subject === "string" ? row.subject.trim() : "";
  if (!id || !subject) return null;
  const status: TodoStatus = row.status === "in_progress" || row.status === "completed" ? row.status : "pending";
  return { id, subject, status };
}

function normalizePlan(raw: unknown): PlacedPlan | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const anchorId = typeof row.anchor_id === "string" ? row.anchor_id : "";
  const todos = Array.isArray(row.todos)
    ? row.todos.map(normalizeTodo).filter((todo): todo is TodoItem => todo !== null)
    : [];
  if (!anchorId || todos.length === 0) return null;
  return {
    anchorId,
    planId: typeof row.plan_id === "string" ? row.plan_id : String(row.plan_id ?? todos[0].id),
    scope: typeof row.scope === "string" ? row.scope : "",
    updatedAt: typeof row.updated_at === "number" ? row.updated_at : 0,
    todos,
  };
}

export function normalizePlans(raw: unknown): PlacedPlan[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(normalizePlan).filter((plan): plan is PlacedPlan => plan !== null);
}

/** A reply a worker restart cut off is drawn by the reply that carries it
 *  on, so its plan goes there too. */
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

/** The plans to draw under each transcript row, in the order they were
 *  first placed. Plans whose turn is not in the transcript (another
 *  branch, a sub-agent's own conversation, a page not loaded) are left out. */
export function plansByRow(
  plans: readonly PlacedPlan[],
  rowIds: readonly string[],
  rowsById: Readonly<Record<string, ChatMsg | undefined>>,
): Map<string, PlacedPlan[]> {
  const rows = new Set(rowIds);
  const out = new Map<string, PlacedPlan[]>();
  for (const plan of plans) {
    const row = placedAnchor(plan.anchorId, rowsById);
    if (!rows.has(row)) continue;
    const list = out.get(row);
    if (list) list.push(plan);
    else out.set(row, [plan]);
  }
  return out;
}

/** How the turn that placed a plan stands. */
export type TurnState = "running" | "ended" | "stopped" | "failed";

/** `settled`: the turn's execution is already terminal although the reply
 *  has not caught up (the bubble reads it the same way). */
export function turnState(msg: ChatMsg | undefined, settled: boolean): TurnState {
  const status = msg?.status;
  if (isStopped(status) || status === "paused") return "stopped";
  if (status === "error") return "failed";
  if (isLive(status) && !settled) return "running";
  return "ended";
}

/** Sub-agents the reply started that have not finished. */
export function runningAgentCount(msg: ChatMsg | undefined): number {
  return (msg?.attachCards ?? []).filter((card) => isAgentRunning(card.attach?.status)).length;
}

/** A running turn's list shows, and so does one whose sub-agents still run.
 *  Otherwise only a turn cut short keeps its unfinished work, and only until
 *  the user writes again; a turn that simply ended clears its list. */
export function planShown(plan: PlacedPlan, state: TurnState, latest: boolean, agents = 0): boolean {
  if (state === "running" || agents > 0) return true;
  if (state === "ended" || !latest) return false;
  return plan.todos.some((todo) => todo.status !== "completed");
}

/** A new turn with no plan of its own shows the unfinished plan from before
 *  it, marked as such. */
export function carryUnfinishedPlan(
  byRow: Map<string, PlacedPlan[]>,
  plans: readonly PlacedPlan[],
  rowIds: readonly string[],
  rowsById: Readonly<Record<string, ChatMsg | undefined>>,
): void {
  const last = rowIds.at(-1);
  const reply = last ? rowsById[last] : undefined;
  if (!last || reply?.role !== "assistant" || !isLive(reply.status) || byRow.has(last)) return;
  const plan = plans.filter((item) => item.scope === "").at(-1);
  if (plan?.todos.some((todo) => todo.status !== "completed")) byRow.set(last, [{ ...plan, stale: true }]);
}

const MAX_VISIBLE_STEPS = 12;

export interface PlanWindow {
  visible: TodoItem[];
  hiddenBefore: number;
  /** Steps past the window still to do. */
  openAfter: number;
}

/** At most twelve steps, starting one before the current step. */
export function planWindow(todos: readonly TodoItem[]): PlanWindow {
  const running = todos.findIndex((todo) => todo.status === "in_progress");
  const current = running >= 0 ? running : todos.findIndex((todo) => todo.status !== "completed");
  const anchor = current >= 0 ? current : todos.length;
  const start = Math.max(0, Math.min(anchor - 1, todos.length - MAX_VISIBLE_STEPS));
  const after = todos.slice(start + MAX_VISIBLE_STEPS);
  return {
    visible: todos.slice(start, start + MAX_VISIBLE_STEPS),
    hiddenBefore: start,
    openAfter: after.filter((todo) => todo.status !== "completed").length,
  };
}

interface TodoState {
  plansBySession: Record<string, PlacedPlan[]>;
  setPlans(sessionId: string, plans: unknown): void;
}

export const useTodoStore = create<TodoState>((set) => ({
  plansBySession: {},
  setPlans: (sessionId, plans) => set((state) => ({
    plansBySession: { ...state.plansBySession, [sessionId]: normalizePlans(plans) },
  })),
}));
